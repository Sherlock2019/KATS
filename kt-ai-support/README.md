# KT AI Support

Kepner-Tregoe troubleshooting with an **AI diagnostic memory**: structured
incidents in PostgreSQL, retrievable by a local LLM, so each solved case makes
the next one cheaper.

Everything runs on one machine. No cloud AI provider is contacted.

```bash
cd kt-ai-support
./start.sh
```

---

## The one architectural decision everything else follows from

**PostgreSQL is the knowledge model. pgvector is one retrieval mechanism
layered on top of it.**

`rag_chunks` is derived data. Drop it, run `POST /api/rag/reindex-all`, and
nothing is lost. If a chunk and its ticket ever disagree, the ticket is right.

That is why there are twelve tables instead of one with a notes column. Every
kind of thing an engineer can be wrong about is stored as its own kind of
record, and the kinds are never collapsed:

| | Table | Why it is separate |
|---|---|---|
| **FACT** | `support_tickets`, `ticket_timeline` | what was observed |
| **SPECIFICATION** | `kt_specifications` | IS / IS NOT, one row per entry per dimension |
| **DISTINCTION** | `kt_distinctions` | what differs between them |
| **CHANGE** | `kt_changes` | linked to the distinction it sits on |
| **HYPOTHESIS** | `kt_hypotheses` | a guess — including the ones that were wrong |
| **EVIDENCE** | `ticket_evidence` | FOR / AGAINST / NEUTRAL |
| **TEST + RESULT** | `diagnostic_tests` | both expected branches, recorded before the run |
| **ROOT CAUSE** | `root_causes` | `SUSPECTED → CONFIRMED`, earned not asserted |
| **ACTION** | `ticket_actions` | with before/after metrics |

An LLM that cannot tell a hypothesis from a confirmed root cause will state
guesses as fact. That distinction cannot be recovered downstream if the schema
threw it away.

---

## Does IS / IS NOT actually improve retrieval?

This is the claim the whole design rests on, so it is measured rather than
asserted. `./start.sh --evaluate` runs the same probes under three
configurations.

The corpus contains **five near-twin cases**. All five present as
*"HTTP 404, could not find token"*. All five have **different root causes**:

| | Symptom is identical. Only the contrast differs. | Actual cause |
|---|---|---|
| 1 | new tokens fail · **existing tokens work** | token never persisted |
| 2 | one cluster fails · **the twin cluster works** | stale secret version |
| 3 | one node fails · **other nodes work** | clock skew |
| 4 | aged tokens fail · **fresh tokens work** | cache miss not falling through |
| 5 | via load balancer fails · **direct to pod works** | header stripped at ingress |

Each probe uses **exactly the same query text** — the symptom, as an engineer
would write it before knowing the answer. Only the KT specification differs.
Semantic and keyword similarity therefore produce an identical ranking for all
five and can be right at most once by luck:

```
  config        Recall@5     MRR  RootCause  KTmatch
  --------------------------------------------------
  vector            75%   0.573        75%    0.452
  hybrid            75%   0.573        75%    0.452
  hybrid+kt        100%   1.000       100%    0.521

  hybrid+kt vs vector-only: MRR +0.427, Recall@5 +25%
```

Vector-only returns case 1 for all five. Adding the KT term finds every one at
rank 1.

The mechanism is in `KTAnalysisService.kt_similarity`. Its important rule is
counter-intuitive: a candidate whose **IS matches but whose IS NOT
contradicts** scores *below* a candidate with no IS NOT at all. A wrong
contrast means the two incidents divide the world differently, so the
historical cause cannot explain the new boundary — that is worse evidence than
a missing contrast, not better.

---

## Retrieval

Seven signals, fused with configurable weights (`RETRIEVAL_WEIGHTS`):

| Signal | Default | What it catches |
|---|---|---|
| `semantic` | 0.40 | *"VM won't boot"* ≈ *"instance fails to spawn"* |
| `keyword` | 0.20 | the exact string someone pasted |
| `metadata` | 0.15 | same product, component, version, environment |
| `error_signature` | 0.10 | the same fault under different timestamps and UUIDs |
| `knowledge_quality` | 0.10 | how reusable the source case is |
| `root_cause_confidence` | 0.05 | a CONFIRMED cause outranks a SUSPECTED one |
| `kt_match` | 0.30 | IS / IS NOT agreement |

Candidates are gathered broadly in SQL (a vector pass ∪ a keyword pass) and
scored in Python. Fusing in SQL would produce one opaque number; the per-term
breakdown is what `POST /api/rag/inspect` returns, and it is the only way to
answer *"why did the wrong case win?"*.

Error signatures are normalised before comparison — timestamps, UUIDs, request
ids, IPs and quantities are stripped, but meaningful constants like
`169.254.169.254` survive, because that address *means* "metadata service".

---

## Guardrails that are enforced, not advised

Each of these is a test in `backend/tests/test_acceptance.py`.

- **A historical fix never becomes a confirmed cause.** `confirmed_root_cause`
  is populated only from *this* ticket's own CONFIRMED row, whatever the model
  writes. A matching case tells you where to look, not what is true.
- **Invented citations are dropped.** Every `source_tickets` entry is checked
  against what was actually retrieved, and discarded with a warning otherwise.
- **CONFIRMED must be earned.** Claiming it without a verification method and
  result, or a test on this ticket returning `CONFIRMS`, is downgraded to
  `HIGH_CONFIDENCE` and the caller is told why. CONFIRMED carries the top
  retrieval boost, so it has to mean something.
- **Rejecting a hypothesis requires a reason** — HTTP 422 otherwise. A
  rejection with no reason cannot stop the next engineer re-running the test.
- **A one-sided test is flagged.** A test with no recorded failing branch
  cannot discriminate: whatever happens, someone reads it as confirmation.
- **`error_signature_norm` is derived, never supplied.** If a client could set
  it, two records of one fault would stop matching.

The system prompt carries the same rules (`app/prompts/diagnostic.py`), but the
enforcement is in the code, because a prompt is a request and a check is not.

---

## The assistant degrades instead of failing

`/api/ai/diagnose` never returns 503 because a model is down. Retrieval, the
similar cases, the rejected causes and the diagnostic-value ranking are all
computed from the database. With no LLM reachable you still get a real answer
built from those facts, and `warnings` says exactly what was unavailable.

That ordering is deliberate: a small local model asked to both *find* and
*reason* does neither well, and every number it invents is one nobody can
check. Here the citations and the ranking are arithmetic; the model only
writes the prose over an evidence pack that is already correct.

**Next best action** ranks by uncertainty removed per unit of cost, not by
which cause looks likeliest:

```
probability × information_gain × discrimination / (cost + risk + time)
```

Information gain peaks at p=0.5 — testing a candidate you are already 95% sure
about teaches you almost nothing — and an irreversible test is penalised 3×, so
a reversible test that settles 40% beats an irreversible one that settles 90%.

There is a shortcut before any of that: if no dimension has both an IS and an
IS NOT, the recommended action is **a question, not a test**. The cheapest test
is the one you never have to run.

---

## API

```
POST   /api/tickets                          GET  /api/tickets
GET    /api/tickets/{id}                     PATCH /api/tickets/{id}
GET    /api/tickets/{id}/completeness        GET  /api/tickets/{id}/similar
POST   /api/tickets/{id}/reindex

POST   /api/tickets/{id}/kt-specifications   GET  /api/tickets/{id}/kt-specifications
POST   /api/tickets/{id}/distinctions        POST /api/tickets/{id}/changes
POST   /api/tickets/{id}/hypotheses          PATCH /api/hypotheses/{id}
POST   /api/tickets/{id}/evidence            POST /api/tickets/{id}/tests
PATCH  /api/tests/{id}                       POST /api/tickets/{id}/actions
POST   /api/tickets/{id}/root-cause          POST /api/tickets/{id}/timeline

POST   /api/rag/search                       POST /api/rag/inspect
GET    /api/rag/chunks/{ticket_id}           GET  /api/rag/queries
POST   /api/rag/reindex-all

POST   /api/ai/diagnose                      POST /api/ai/next-action
POST   /api/ai/next-question                 GET  /health

GET    /api/problems                         GET  /api/problems/emerging
GET    /api/problems/{id}                    PATCH /api/problems/{id}
POST   /api/problems/recluster
```

---

## The problem register, and detecting what is emerging

Everything above is **reactive**: it answers a question somebody asked. The
problem register is the half that does not wait to be asked.

Tickets are clustered by `error_signature_norm` — already populated on every
row, legacy imports included — into `problem_records`. Clustering is a
wholesale rebuild rather than an incremental update: it is one query plus a
dictionary (61 ms over 323 tickets), and a rebuild that cannot drift beats an
incremental one that can.

**Human-owned fields survive the rebuild.** `status`, `permanent_fix`, `owner`,
`notes` and `kb_ref` are never written by clustering — an engineer marking a
cluster as a known error with a documented fix must not have that erased by the
next scheduled run. There is a test for exactly this.

### Every statistic carries its denominators

The register reports on a corpus where some root causes were **verified by a
human** and others were **extracted by a model from legacy free text**. Those
are not the same evidence, and averaging them produces confident nonsense. So
every response carries a generated sentence stating what the numbers do and do
not support:

> *"44 incidents share this signature. **None has a verified cause, so the
> grouping is a pattern, not a finding.**"*
>
> *"5 incidents share this signature. 5 have a verified cause, and 1 of those 5
> point at the same thing."*

The failure mode this exists to prevent is a dashboard that shows
`dominant_cause` next to `member_count` and lets a reader infer that all 44
were caused by it.

### Surge detection, and the alert nobody should get

`GET /api/problems/emerging` compares a recent window against a baseline rate.
A ratio **alone** is not enough at these counts. A cluster running at a steady
1/week produces 2 in some weeks purely by chance — that is a 2.1× "spike", and
an alert that fires on it teaches people to ignore the channel.

Incident arrivals are roughly Poisson, so the noise on an expected count of λ
is √λ. The recent count must clear **λ + 3√λ** as well as the ratio:

| Baseline λ | Noise floor | Observed | Verdict |
|---|---|---|---|
| 0.93 / week | 3.8 | 2 | no alert — ordinary variation |
| 0.30 / week | 2.0 | 8 | **ALERT** — real |

**Multi-customer is an independent trigger.** The same fault on two or more
tenants inside one window is a platform problem however slowly it arrives, so
it alerts without needing an acceleration.

Both the trigger that fired and the floor it cleared are persisted and
reported, because a spread alert explained as a rate — *"1.0× surge, ALERT"* —
reads as a broken detector and gets muted:

> *"7 in the last window across 28 customers — the same fault on more than one
> tenant, so it is a platform problem rather than a configuration one."*

---

## Models

Two models, two jobs — a chat model cannot produce embeddings.

| | Default | Size | Note |
|---|---|---|---|
| Generation | `phi3` | 2.2 GB | ~1 min per grounded answer on CPU |
| | `gemma4:latest` | 9.6 GB | reasons better, minutes per answer |
| Embeddings | `embeddinggemma` | 621 MB | 768-dim, matches the schema |

**The embedding dimension is not hardcoded.** The migration carries
`${EMBEDDING_DIM}`; `migrations/run.py` substitutes it from config and
**refuses** to apply a schema that disagrees with a populated column:

```
REFUSING TO MIGRATE
rag_chunks.embedding is vector(768); embeddinggemma produces 1024.
Vectors of different dimensions cannot be compared, so this would
silently corrupt retrieval.
```

Changing the embedder is a real migration, not a config tweak — every stored
vector becomes meaningless.

---

## Indexing is cheap enough to run on every write

Regenerating chunks is CPU and takes milliseconds; embedding is a network round
trip and takes seconds. So the builder always regenerates, compares content
hashes, and embeds **only what moved**. A ticket edited twenty times during an
incident costs twenty cheap rebuilds and a handful of embeddings.

Sixteen chunk types, one per meaning — `PROBLEM`, `SYMPTOM`, `CONTEXT`,
`KT_SPECIFICATION`, `DISTINCTIONS`, `CHANGES`, `HYPOTHESIS`,
`REJECTED_HYPOTHESIS`, `EVIDENCE`, `DIAGNOSTIC_TEST`, `ROOT_CAUSE`,
`WORKAROUND`, `RESOLUTION`, `PREVENTION`, `TIMELINE`, `FULL_CASE_SUMMARY`.

`REJECTED_HYPOTHESIS` carries a deliberately **high** confidence score. Knowing
something was ruled out, and how, is reliable knowledge even though the
hypothesis was wrong — and it is the most under-used content in any ticket
system.

---

## Commands

```bash
./start.sh                 # install, migrate, seed, serve
./start.sh --test          # 32 tests: unit + acceptance
./start.sh --evaluate      # vector vs hybrid vs hybrid+KT
./start.sh --reset         # rebuild the schema from scratch
./start.sh stop            # stop the API and Postgres

cd backend
python -m migrations.run --status
python -m scripts.seed_demo_cases --reset
python -m scripts.evaluate_retrieval --verbose
python -m pytest tests/test_problem_detection.py -q    # the surge detector
```

Rebuilding the problem register, and asking what is emerging:

```bash
curl -X POST 'localhost:8100/api/problems/recluster?window_days=7&baseline_days=90'
curl localhost:8100/api/problems/emerging
```

Cheap enough to put on a timer — the rebuild above runs in ~60 ms over the
whole corpus.

---

## Demo corpus

23 cases across authentication, Kubernetes, database, network, storage, API,
certificate, DNS, CPU, memory, deployment, configuration, permissions, cloud
infrastructure and application failure — including unresolved cases, rejected
hypotheses, workarounds without fixes, and the five near-twins above.

Knowledge-quality scores range 0.46–0.84, so retrieval ranking has something
real to discriminate on.

---

## Status

Backend complete and tested: schema, CRUD, the KT model, chunk builder, local
embeddings, hybrid + KT retrieval, the diagnostic assistant, next-action,
next-question, and the inspector.

**Not built:** the React/TypeScript frontend (§26, §27, §32, §43). The API
surface it needs is complete and documented at `/docs`.

**Laptop configuration:** CORS is open, Postgres has a default password, and
the API has no authentication. All three need fixing before this leaves
127.0.0.1.
