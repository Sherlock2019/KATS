"""Stand up a synthetic legacy CORE database.

The point is to prove the whole ingestion path before anyone grants access to
a production system. It builds normalised tables with the awkward shape a real
legacy system has - and then the six views on top, so the connector reads
exactly the contract it will read in production.

Deliberately messy, because a clean fixture proves nothing:

  * column names that do not match ours (`subject`, `sev`, `cust_name`)
  * severities as a mix of "1", "P1", "Critical"
  * roughly a third of tickets never resolved
  * some threads too short to be worth extracting
  * a handful with no IS NOT anywhere in them, to prove the extractor
    returns null instead of inventing one

    python -m scripts.make_fake_core --url sqlite:///fake_core.db --tickets 500
    python -m scripts.make_fake_core --url mysql+pymysql://root:x@127.0.0.1:3307/core
"""

from __future__ import annotations

import argparse
import pathlib
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine, text  # noqa: E402

UTC = timezone.utc
random.seed(20260827)

PRODUCTS = [
    ("OpenStack", "nova-compute"), ("OpenStack", "neutron-ovn"),
    ("OpenStack", "keystone"), ("OpenStack", "cinder"),
    ("PostgreSQL", "replication"), ("PostgreSQL", "connection-pool"),
    ("Kubernetes", "kubelet"), ("Kubernetes", "coredns"),
    ("EdgeProxy", "tls"), ("ObjectStore", "iam"),
]
CUSTOMERS = ["HN-Bank", "Helios Retail", "Vantage Logistics",
             "Northwind Bank", "Aurora Media"]
ENVIRONMENTS = ["prod", "production", "staging", "prod-eu", "prod-sg"]
SEVERITIES = ["1", "P1", "Critical", "2", "P2", "High", "3", "Normal", "4", "Low"]

# (symptom, cause, fix, has_is_not) — the last flag is what lets the test
# assert that a thread with no stated contrast yields a null IS NOT.
SCENARIOS = [
    ("Authentication returns HTTP 404 could not find token",
     "tokens were generated but never persisted to the token store",
     "corrected the persistence transaction and regenerated tokens", True),
    ("VM creation fails, guest hangs at cloud-init",
     "the OVN metadata port was not recreated after the agent restarted",
     "restarted neutron-ovn-metadata-agent and set force_config_drive", True),
    ("Intermittent SERVFAIL on internal DNS lookups",
     "CoreDNS was under-provisioned for peak query volume",
     "scaled CoreDNS from 2 to 6 replicas", True),
    ("too many clients already during the nightly batch",
     "batch parallelism was raised without a per-workload connection cap",
     "gave the batch job its own pool with a hard cap", True),
    ("TLS handshake fails for API clients after certificate renewal",
     "the renewal deployed the leaf without the intermediate",
     "redeployed the full chain", True),
    ("Replica lag grows from seconds to hours",
     "an ACL change scoped the replication port to the local VPC",
     "restored the ACL rule for the peer region", False),
    ("Pods crash-loop with exec format error",
     "the node pool was recreated with arm64 while images are amd64",
     "rebuilt images as multi-arch", True),
    ("Disk fills on the log aggregator within hours",
     "TRACE logging was left enabled on one service",
     "reverted the log level", False),
]

DDL = [
    """CREATE TABLE core_ticket (
        id VARCHAR(32) PRIMARY KEY, subject VARCHAR(500), cust_name VARCHAR(200),
        org VARCHAR(100), prod VARCHAR(100), comp VARCHAR(100), rel VARCHAR(50),
        env VARCHAR(50), sev VARCHAR(20), state VARCHAR(50), queue VARCHAR(100),
        assignee VARCHAR(100), reporter VARCHAR(100), node VARCHAR(100),
        region VARCHAR(50), open_date TIMESTAMP, close_date TIMESTAMP,
        last_update TIMESTAMP)""",
    """CREATE TABLE core_ticket_note (
        note_id VARCHAR(40) PRIMARY KEY, tkt VARCHAR(32), ord_no INTEGER,
        note_time TIMESTAMP, author_type VARCHAR(20), note_text TEXT)""",
    """CREATE TABLE core_ticket_close (
        tkt VARCHAR(32) PRIMARY KEY, close_notes TEXT, close_code VARCHAR(50),
        closed_by VARCHAR(100))""",
    """CREATE TABLE core_attachment (
        att_id VARCHAR(40) PRIMARY KEY, tkt VARCHAR(32), fname VARCHAR(255),
        mime VARCHAR(100), uri VARCHAR(500))""",
    """CREATE TABLE core_kb (
        kb_id VARCHAR(32) PRIMARY KEY, headline VARCHAR(500), prod VARCHAR(100),
        comp VARCHAR(100), rel VARCHAR(50), article_body TEXT,
        pub_state VARCHAR(20), last_update TIMESTAMP)""",
    """CREATE TABLE core_kb_ticket (
        kb_id VARCHAR(32), tkt VARCHAR(32), link_kind VARCHAR(40))""",
]

# The six views. In production a DBA writes these; here they prove the
# connector only ever needs the contract, not the underlying schema.
VIEWS = [
    """CREATE VIEW v_ticket AS SELECT
        id, subject AS title, cust_name AS customer, org AS organization,
        prod AS product, comp AS component, rel AS version, env AS environment,
        sev AS severity, state AS status, queue, assignee, reporter,
        node, region, open_date AS opened, close_date AS closed,
        last_update AS updated_at
      FROM core_ticket""",
    """CREATE VIEW v_ticket_message AS SELECT
        tkt AS ticket_id, ord_no AS seq, note_time AS ts,
        author_type AS author_role, note_text AS body
      FROM core_ticket_note""",
    """CREATE VIEW v_ticket_resolution AS SELECT
        tkt AS ticket_id, close_notes AS resolution, close_code AS closure_code,
        closed_by AS resolved_by
      FROM core_ticket_close""",
    """CREATE VIEW v_ticket_attachment AS SELECT
        tkt AS ticket_id, fname AS filename, mime, uri
      FROM core_attachment""",
    """CREATE VIEW v_kb_article AS SELECT
        kb_id AS id, headline AS title, prod AS product, comp AS component,
        rel AS version, article_body AS body, pub_state AS status,
        last_update AS updated_at
      FROM core_kb""",
    """CREATE VIEW v_kb_link AS SELECT
        tkt AS ticket_id, kb_id, link_kind AS relation
      FROM core_kb_ticket""",
]


def build_thread(scenario, base: datetime, resolved: bool, thin: bool):
    """A plausible support conversation."""
    symptom, cause, fix, has_is_not = scenario
    notes = []
    t = base

    def add(role, text_):
        nonlocal t
        t += timedelta(minutes=random.randint(4, 90))
        notes.append((t, role, text_))

    add("customer", f"{symptom}. This started this morning and we need it looked at.")
    if thin:
        add("support", "Investigating.")
        return notes

    add("support", "Thanks — can you confirm when you last saw this working?")
    add("customer", "It was fine yesterday evening. Nothing changed that we know of.")

    if has_is_not:
        add("customer",
            "Worth saying: the same thing works fine on our other environment, "
            "and only the new requests are affected — the existing ones are fine.")

    add("support", "We tried restarting the service — no change.")
    add("support", "Checked connectivity from the host, that responds normally.")

    if resolved:
        add("support", f"Found it. Looks like {cause}.")
        add("support", f"We {fix}. Please confirm.")
        add("customer", "Confirmed, working now. Thanks.")
    else:
        add("support", "Still investigating, will update tomorrow.")
    return notes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="sqlite:///fake_core.db")
    parser.add_argument("--tickets", type=int, default=500)
    parser.add_argument("--kb", type=int, default=20)
    args = parser.parse_args()

    engine = create_engine(args.url)
    print(f"\n  building fake CORE in {args.url}")

    with engine.begin() as conn:
        for name in ("v_ticket", "v_ticket_message", "v_ticket_resolution",
                     "v_ticket_attachment", "v_kb_article", "v_kb_link"):
            conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
        for name in ("core_ticket_note", "core_ticket_close", "core_attachment",
                     "core_kb_ticket", "core_ticket", "core_kb"):
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
        for stmt in DDL:
            conn.execute(text(stmt))

    now = datetime.now(UTC)
    rows = notes = closes = 0

    with engine.begin() as conn:
        # KB first, so tickets can link to it.
        for k in range(args.kb):
            scenario = SCENARIOS[k % len(SCENARIOS)]
            product, component = PRODUCTS[k % len(PRODUCTS)]
            conn.execute(text("""INSERT INTO core_kb
                (kb_id, headline, prod, comp, rel, article_body, pub_state, last_update)
                VALUES (:i,:h,:p,:c,:r,:b,:s,:u)"""), {
                "i": f"KB-{1000 + k}",
                "h": f"Troubleshooting: {scenario[0][:80]}",
                "p": product, "c": component, "r": "2.4",
                "b": (f"SYMPTOMS\n{scenario[0]}\n\n"
                      f"DIAGNOSTICS\nCheck the component logs and confirm the "
                      f"comparable healthy case.\n\n"
                      f"CAUSE\n{scenario[1]}\n\n"
                      f"RESOLUTION\n{scenario[2]}\n\n"
                      f"PREVENTION\nAdd monitoring for the precondition."),
                "s": "published", "u": now - timedelta(days=random.randint(1, 900)),
            })

        for n in range(args.tickets):
            scenario = random.choice(SCENARIOS)
            product, component = random.choice(PRODUCTS)
            resolved = random.random() > 0.32
            thin = random.random() < 0.18
            opened = now - timedelta(days=random.randint(1, 2400),
                                     hours=random.randint(0, 23))
            closed = opened + timedelta(hours=random.randint(2, 96)) if resolved else None
            tid = f"INC{700000 + n}"

            conn.execute(text("""INSERT INTO core_ticket
                (id, subject, cust_name, org, prod, comp, rel, env, sev, state,
                 queue, assignee, reporter, node, region, open_date, close_date, last_update)
                VALUES (:id,:su,:cu,:og,:pr,:co,:re,:en,:se,:st,:qu,:as,:rp,:no,:rg,:op,:cl,:lu)"""), {
                "id": tid, "su": scenario[0][:200],
                "cu": random.choice(CUSTOMERS), "og": "acct-" + str(random.randint(1, 40)),
                "pr": product, "co": component, "re": random.choice(["2.4", "3.1", "16.2"]),
                "en": random.choice(ENVIRONMENTS), "se": random.choice(SEVERITIES),
                "st": "Closed" if resolved else random.choice(["Open", "Pending"]),
                "qu": "L2 Cloud Ops", "as": "eng-" + str(random.randint(1, 12)),
                "rp": "user-" + str(random.randint(1, 200)),
                "no": f"node-{random.randint(1, 40):02d}",
                "rg": random.choice(["HN", "HCMC", "eu-west-1", "us-east-1"]),
                "op": opened, "cl": closed, "lu": closed or opened,
            })
            rows += 1

            for seq, (ts, role, body) in enumerate(
                    build_thread(scenario, opened, resolved, thin), start=1):
                conn.execute(text("""INSERT INTO core_ticket_note
                    (note_id, tkt, ord_no, note_time, author_type, note_text)
                    VALUES (:i,:t,:o,:ts,:a,:b)"""),
                    {"i": f"{tid}-{seq}", "t": tid, "o": seq, "ts": ts,
                     "a": role, "b": body})
                notes += 1

            if resolved:
                # Only some closures carry a verifying code — that split is
                # what separates legacy_verified from legacy_extracted.
                code = random.choice(
                    ["resolved-verified", "fixed", "solved",
                     "closed-no-fault", "customer-closed", "duplicate"])
                conn.execute(text("""INSERT INTO core_ticket_close
                    (tkt, close_notes, close_code, closed_by) VALUES (:t,:n,:c,:b)"""),
                    {"t": tid, "n": scenario[2], "c": code,
                     "b": "eng-" + str(random.randint(1, 12))})
                closes += 1

                if random.random() < 0.35:
                    conn.execute(text("""INSERT INTO core_kb_ticket
                        (kb_id, tkt, link_kind) VALUES (:k,:t,'resolved_by')"""),
                        {"k": f"KB-{1000 + random.randint(0, args.kb - 1)}", "t": tid})

    with engine.begin() as conn:
        for stmt in VIEWS:
            conn.execute(text(stmt))

    print(f"  {rows} tickets · {notes} messages · {closes} closures · {args.kb} KB articles")
    print(f"  six views created\n")
    print(f"  LEGACY_DATABASE_URL={args.url}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
