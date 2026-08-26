"""RetrievalService — §19-§22. The hybrid search.

Vector similarity alone is not enough, and the failure is specific: an
engineer pastes an exact error string, and cosine distance returns three
tickets that are *about* authentication instead of the one that produced
that string. Conversely, keyword search alone misses "VM won't boot" against
"instance fails to spawn".

So seven signals, fused with configurable weights (§20):

    semantic                pgvector cosine
    keyword                 Postgres full-text over the chunk
    metadata                product / component / version / environment match
    error_signature         normalised exact-ish error match
    knowledge_quality       how reusable the source case is (§30)
    root_cause_confidence   a CONFIRMED cause outranks a SUSPECTED one
    kt_match                IS / IS NOT agreement (§22)

The last one is the addition that matters. It is what lets a case whose
IS *and* IS NOT both match outrank a case that is semantically closer but
draws the boundary differently — see KTAnalysisService.kt_similarity.

Candidates are gathered broadly (SQL, cheap) and scored in Python
(expressive, and the per-term breakdown is what /rag-inspector needs).
Fusing in SQL would give one opaque number nobody can debug.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import SupportTicket
from app.services.embeddings.service import get_embedding_service
from app.services.kt.analysis import KTAnalysisService, KTProfile
from app.services.kt.signature import normalize_error_signature, signature_similarity

log = logging.getLogger("kt.retrieval")

_CONFIDENCE_RANK = {
    "CONFIRMED": 1.0,
    "HIGH_CONFIDENCE": 0.7,
    "PROBABLE": 0.45,
    "SUSPECTED": 0.2,
    "UNKNOWN": 0.0,
}

# Fields compared for the metadata term, and what each is worth. Product and
# component dominate: §21 says a matching product+component+error_code should
# beat a generic semantic match, and this is where that happens.
_METADATA_FIELDS = {
    "product": 0.30,
    "component": 0.25,
    "error_code": 0.20,
    "environment_type": 0.10,
    "product_version": 0.08,
    "service": 0.07,
}


@dataclass
class Candidate:
    chunk_id: uuid.UUID
    ticket_id: uuid.UUID
    ticket_number: str
    chunk_type: str
    title: str | None
    content: str
    metadata: dict

    quality: float = 0.0
    confidence: float = 0.0

    product: str | None = None
    component: str | None = None
    environment: str | None = None
    error_code: str | None = None
    error_signature: str | None = None
    severity: str | None = None
    status: str | None = None
    root_cause_status: str | None = None

    semantic: float = 0.0
    keyword: float = 0.0
    metadata_score: float = 0.0
    signature: float = 0.0
    kt: float = 0.0
    final: float = 0.0
    why: list[str] = field(default_factory=list)


@dataclass
class QueryContext:
    """What we are searching for, in every form the scorers need."""

    text: str
    detected: dict = field(default_factory=dict)
    signature: str = ""
    kt_profile: KTProfile | None = None
    exclude: set[uuid.UUID] = field(default_factory=set)


class RetrievalService:
    # ------------------------------------------------------------------
    # query construction
    # ------------------------------------------------------------------
    @staticmethod
    def context_from_ticket(ticket: SupportTicket, extra_question: str = "") -> QueryContext:
        """Build the query from the ticket's own structure.

        Crucially this includes the KT specification, not just the prose:
        without it there is no IS/IS NOT to match against and §22 cannot fire.
        """
        profile = KTAnalysisService.build_profile(ticket.specifications)
        kt_text = KTAnalysisService.to_text(profile)

        parts = [
            ticket.title,
            ticket.problem_summary,
            ticket.actual_behavior,
            ticket.error_message,
            kt_text,
            "; ".join(d.distinction for d in ticket.distinctions),
            "; ".join(c.description for c in ticket.changes),
            extra_question,
        ]
        return QueryContext(
            text="\n".join(p for p in parts if p),
            detected={
                "product": ticket.product,
                "product_version": ticket.product_version,
                "service": ticket.service,
                "component": ticket.component,
                "environment_type": ticket.environment_type,
                "error_code": ticket.error_code,
            },
            signature=ticket.error_signature_norm or normalize_error_signature(ticket.error_message),
            kt_profile=profile,
            exclude={ticket.id},
        )

    @staticmethod
    def context_from_text(query: str, filters: dict | None = None) -> QueryContext:
        """Free-text search. Pull what metadata we can out of the string.

        Only patterns that are unambiguous — an HTTP status, a quoted error.
        Guessing a product name from prose produces filters nobody asked for
        and silently hides the right answer.
        """
        detected = dict(filters or {})

        if "error_code" not in detected:
            status = re.search(r"\b(?:HTTP\s*)?([45]\d{2})\b", query)
            if status:
                detected["error_code"] = status.group(1)

        quoted = re.findall(r'"([^"]{8,})"', query)
        signature = normalize_error_signature(quoted[0] if quoted else query)

        return QueryContext(text=query, detected=detected, signature=signature)

    # ------------------------------------------------------------------
    # candidate gathering
    # ------------------------------------------------------------------
    @staticmethod
    def _gather(db: Session, ctx: QueryContext, embedding: list[float] | None,
                chunk_types: list[str] | None, filters: dict, pool: int) -> dict[uuid.UUID, Candidate]:
        """Union of a vector pass and a keyword pass.

        Union, not intersection: each modality is here precisely to catch what
        the other misses, so requiring both would discard exactly the results
        the hybrid exists for.
        """
        where = ["TRUE"]
        params: dict = {"pool": pool}

        if chunk_types:
            where.append("c.chunk_type = ANY(:chunk_types)")
            params["chunk_types"] = list(chunk_types)

        if ctx.exclude:
            where.append("c.ticket_id <> ALL(:exclude)")
            params["exclude"] = list(ctx.exclude)

        # Hard filters only when explicitly requested. §21 prefers boosting
        # over filtering: a hard filter on a wrong guess returns nothing, and
        # "no results" is a much worse failure than "ranked third".
        for key, value in (filters or {}).items():
            if value in (None, "", []):
                continue
            column = {
                "product": "t.product", "component": "t.component",
                "environment": "t.environment", "environment_type": "t.environment_type",
                "error_code": "t.error_code", "severity": "t.severity",
                "status": "t.status", "root_cause_status": "t.root_cause_status",
                "cloud_provider": "t.cloud_provider", "region": "t.region",
            }.get(key)
            if column:
                where.append(f"{column} = :flt_{key}")
                params[f"flt_{key}"] = value

        clause = " AND ".join(where)
        select_cols = """
            c.id AS chunk_id, c.ticket_id, c.chunk_type, c.title, c.content,
            c.metadata, c.quality_score, c.confidence_score,
            t.ticket_number, t.product, t.component, t.environment, t.error_code,
            t.error_signature_norm, t.severity, t.status, t.root_cause_status
        """

        candidates: dict[uuid.UUID, Candidate] = {}

        def absorb(rows, kind: str):
            for row in rows:
                mapping = row._mapping
                cid = mapping["chunk_id"]
                cand = candidates.get(cid)
                if cand is None:
                    cand = Candidate(
                        chunk_id=cid,
                        ticket_id=mapping["ticket_id"],
                        ticket_number=mapping["ticket_number"],
                        chunk_type=mapping["chunk_type"],
                        title=mapping["title"],
                        content=mapping["content"],
                        metadata=mapping["metadata"] or {},
                        quality=float(mapping["quality_score"] or 0),
                        confidence=float(mapping["confidence_score"] or 0),
                        product=mapping["product"],
                        component=mapping["component"],
                        environment=mapping["environment"],
                        error_code=mapping["error_code"],
                        error_signature=mapping["error_signature_norm"],
                        severity=mapping["severity"],
                        status=mapping["status"],
                        root_cause_status=mapping["root_cause_status"],
                    )
                    candidates[cid] = cand
                if kind == "vector":
                    # pgvector cosine distance -> similarity
                    cand.semantic = max(0.0, 1.0 - float(mapping["distance"]))
                else:
                    cand.keyword = min(1.0, float(mapping["rank"]) * 4)

        if embedding is not None:
            rows = db.execute(
                text(f"""
                    SELECT {select_cols}, c.embedding <=> CAST(:emb AS vector) AS distance
                    FROM rag_chunks c JOIN support_tickets t ON t.id = c.ticket_id
                    WHERE {clause} AND c.embedding IS NOT NULL
                    ORDER BY c.embedding <=> CAST(:emb AS vector)
                    LIMIT :pool
                """),
                {**params, "emb": "[" + ",".join(f"{v:.7g}" for v in embedding) + "]"},
            ).fetchall()
            absorb(rows, "vector")

        keyword_query = " ".join(re.findall(r"[A-Za-z0-9_.\-/]{3,}", ctx.text)[:40])
        if keyword_query.strip():
            rows = db.execute(
                text(f"""
                    SELECT {select_cols},
                           ts_rank(c.tsv, websearch_to_tsquery('english', :q)) AS rank
                    FROM rag_chunks c JOIN support_tickets t ON t.id = c.ticket_id
                    WHERE {clause} AND c.tsv @@ websearch_to_tsquery('english', :q)
                    ORDER BY rank DESC
                    LIMIT :pool
                """),
                {**params, "q": keyword_query},
            ).fetchall()
            absorb(rows, "keyword")

        return candidates

    # ------------------------------------------------------------------
    # scoring
    # ------------------------------------------------------------------
    @staticmethod
    def _metadata_score(ctx: QueryContext, cand: Candidate) -> tuple[float, list[str]]:
        detected = {k: v for k, v in ctx.detected.items() if v}
        if not detected:
            return 0.0, []

        earned = 0.0
        possible = 0.0
        reasons = []
        for field_name, weight in _METADATA_FIELDS.items():
            wanted = detected.get(field_name)
            if not wanted:
                continue
            possible += weight
            have = cand.metadata.get(field_name) or getattr(cand, field_name, None)
            if have and str(have).lower() == str(wanted).lower():
                earned += weight
                reasons.append(f"{field_name}={have}")
        if possible == 0:
            return 0.0, []
        return earned / possible, reasons

    @classmethod
    def score(cls, db: Session, ctx: QueryContext, candidates: dict[uuid.UUID, Candidate],
              weights: dict[str, float]) -> list[Candidate]:
        # KT profiles are per-ticket, and many chunks share a ticket. Build
        # each once — this is the expensive part of scoring.
        kt_cache: dict[uuid.UUID, tuple[float, list[str]]] = {}

        if ctx.kt_profile and ctx.kt_profile.stated_cells:
            ticket_ids = {c.ticket_id for c in candidates.values()}
            if ticket_ids:
                rows = db.execute(
                    text("""
                        SELECT ticket_id, dimension, side, value,
                               structured_key, structured_value, sort_order, created_at
                        FROM kt_specifications WHERE ticket_id = ANY(:ids)
                    """),
                    {"ids": list(ticket_ids)},
                ).fetchall()

                by_ticket: dict[uuid.UUID, list] = {}
                for row in rows:
                    by_ticket.setdefault(row._mapping["ticket_id"], []).append(row._mapping)

                for tid in ticket_ids:
                    specs = by_ticket.get(tid, [])
                    if not specs:
                        kt_cache[tid] = (0.0, [])
                        continue
                    profile = KTAnalysisService.build_profile(
                        [SimpleNamespace(**dict(s)) for s in specs]
                    )
                    kt_cache[tid] = KTAnalysisService.kt_similarity(ctx.kt_profile, profile)

        for cand in candidates.values():
            meta_score, meta_reasons = cls._metadata_score(ctx, cand)
            cand.metadata_score = meta_score

            cand.signature = (
                signature_similarity(ctx.signature, cand.error_signature)
                if ctx.signature and cand.error_signature else 0.0
            )

            kt_score, kt_reasons = kt_cache.get(cand.ticket_id, (0.0, []))
            cand.kt = kt_score

            rc_conf = _CONFIDENCE_RANK.get(str(cand.root_cause_status or "UNKNOWN"), 0.0)

            cand.final = round(
                weights.get("semantic", 0) * cand.semantic
                + weights.get("keyword", 0) * cand.keyword
                + weights.get("metadata", 0) * cand.metadata_score
                + weights.get("error_signature", 0) * cand.signature
                + weights.get("knowledge_quality", 0) * cand.quality
                + weights.get("root_cause_confidence", 0) * rc_conf
                + weights.get("kt_match", 0) * cand.kt,
                6,
            )

            why = []
            if cand.semantic > 0.45:
                why.append(f"semantically similar ({cand.semantic:.0%})")
            if cand.keyword > 0.05:
                why.append("keyword match")
            if cand.signature > 0.6:
                why.append(f"same error signature ({cand.signature:.0%})")
            if meta_reasons:
                why.append("matches " + ", ".join(meta_reasons))
            why.extend(kt_reasons)
            if str(cand.root_cause_status) == "CONFIRMED":
                why.append("confirmed root cause on file")
            cand.why = why

        return sorted(candidates.values(), key=lambda c: c.final, reverse=True)

    # ------------------------------------------------------------------
    # public entry point
    # ------------------------------------------------------------------
    @classmethod
    def search(cls, db: Session, ctx: QueryContext, *, top_k: int = 8,
               chunk_types: list[str] | None = None, filters: dict | None = None,
               weight_override: dict[str, float] | None = None) -> dict:
        started = time.time()
        settings = get_settings()
        embeddings = get_embedding_service()

        weights = settings.weights.normalised()
        if weight_override:
            merged = settings.weights.model_dump()
            merged.update(weight_override)
            total = sum(merged.values()) or 1.0
            weights = {k: v / total for k, v in merged.items()}

        embedding = None
        if ctx.text.strip():
            try:
                embedding = embeddings.embed_text(ctx.text[:8000])
            except Exception as exc:  # noqa: BLE001
                log.warning("query embedding failed, keyword-only for this search: %s", exc)

        candidates = cls._gather(
            db, ctx, embedding, chunk_types, filters or {}, settings.retrieval_candidate_pool
        )
        ranked = cls.score(db, ctx, candidates, weights)

        return {
            "results": ranked[:top_k],
            "weights": weights,
            "detected_metadata": {k: v for k, v in ctx.detected.items() if v},
            "applied_filters": {k: v for k, v in (filters or {}).items() if v},
            "embedding_model": embeddings.model if embedding is not None else None,
            "embed_mode": embeddings.mode,
            "candidates_considered": len(candidates),
            "latency_ms": int((time.time() - started) * 1000),
        }
