# KATS — KT AI Enhanced Ticket Support System

A support ticket workflow built on the **Universal Troubleshooting Action Plan** — a 10-step method
rooted in Kepner-Tregoe problem analysis — with an AI agent that reads your knowledge base, customer
history and problem register to find the **shortest path to resolution**, including telling you not
to troubleshoot at all.

> **Proof of concept.** Runs entirely in a browser, offline, from a single file.
> The AI agent is a **mock** — no model is called. See [Is the AI real?](#is-the-ai-real)

---

## Try it in 30 seconds

```bash
git clone git@github.com:Sherlock2019/KATS.git
cd KATS
./start.sh
```

That lands on a **chooser page**: open the legacy ticket view and KATS side by side on the same
ticket, and compare them.

```bash
./start.sh kats     # straight to KATS (v9)
./start.sh v8       # the previous release, for comparison
./start.sh core     # straight to the legacy view
./start.sh --build  # rebuild the bundle first, then serve
```

No install, no build, no internet. You can also just double-click `rax_ticket_support_page.html`.

---

## Why it exists

Every ticket tool records **what happened to the ticket** — who it was assigned to, which queue it
moved through, when the status changed.

Almost none of them record **what the problem was**.

That single gap causes all of this:

- The same issue gets solved five times by five engineers, each starting from zero.
- A customer reports something you fixed for them four months ago, and nobody knows.
- Two engineers work the same outage on two tickets without realising.
- Hours are spent troubleshooting behaviour that turns out to be *working as designed*.
- Nobody can answer *"what actually breaks us most often?"* — because root causes are free text,
  and free text cannot be counted.
- The engineer who solved it leaves, and the knowledge leaves with them.

KATS closes the gap by capturing the **problem**, not just the ticket — and by making every solved
case teach the next one.

This is **evolution, not replacement**. It keeps what a classic ticket system does well and adds what
it was never built to do: the structure of a real problem analysis, and a memory that outlives the
engineer who wrote it.

---

## The Universal Troubleshooting Pipeline

Domain-neutral — it works for IT, cloud, AI, infrastructure, engineering, manufacturing, operations
and business processes:

**🔴 PRIORITIZE → 🛡️ CONTAIN → 🎯 DEFINE → 🔎 NARROW → 🧪 TEST → ✅ CONFIRM → 🔧 FIX & LEARN**

> **Stop the impact. Narrow the difference. Test one variable. Prove the cause. Fix it forever.**

| # | Stage | Core question | Output |
|---|---|---|---|
| 1 | 🔴 PRIORITIZE | What is affected, and what do we protect first? | Priority + blast radius + **evidence captured** + the action to do first |
| 2 | 🛡️ CONTAIN | How do we reduce the impact safely right now? | Mitigation / workaround, and the impact after it |
| 3 | 🎯 DEFINE | What exactly is failing, and what comparable thing is working? | Deviation + IS / IS NOT |
| 4 | 🔎 NARROW | What is different, and what changed? | Distinctions + changes |
| 5 | 🧪 TEST | Which possible cause can we eliminate next? | Ranked candidates + one single-variable test |
| 6 | ✅ CONFIRM | Can we prove the cause controls the symptom? | Confirmed root cause — or back to NARROW |
| 7 | 🔧 FIX & LEARN | How do we fix it permanently and stop recurrence? | Fix + validation + prevention + KB + watch window |

This replaces the previous 10-step Kepner-Tregoe sequence for the operator. **The method underneath is
unchanged** — the ten steps collapse onto these seven in clean pairs with no orphans, and
`test_pipeline.js` asserts that mapping so an archived ticket can always be migrated:

| Stage | Absorbs |
|---|---|
| PRIORITIZE | 1 Protect + 2 Prioritize |
| CONTAIN | 3 Mitigate |
| DEFINE | 4 State + 5 Specify |
| NARROW | 6 Isolate |
| TEST | 7 Hypothesize + 8 Eliminate |
| CONFIRM | 9 Verify |
| FIX & LEARN | 10 Correct & Prevent |

### It is not a line — the middle is a loop

```
PRIORITIZE → CONTAIN → DEFINE →  ┌─ NARROW → TEST → CONFIRM ─┐ → FIX & LEARN
                             └────────── REFUTED ────────┘
```

NARROW → TEST → CONFIRM cycles: every refuted candidate sends you back to NARROW. The pipeline bar
draws those three inside one bracket with a **pass counter**, because a flat seven-chip bar tells an
operator on their third loop that nothing has moved. Inside the loop, progress is measured in
**candidates eliminated**, not stages passed.

The objective is never an ever-growing list of possibilities. It is to **continuously reduce the
search space until only a defensible cause remains**.

### Four properties that are enforced, not advised

1. **Evidence capture is an output of PRIORITIZE**, not a footnote in containment — and CONTAIN stays
   *blocked* until something is captured. By the time anyone is mitigating, the state they needed is
   usually already gone.
2. **Stage status is computed from the fields.** There is no way to click a stage to "done". An
   operator's own status can *downgrade* a stage (fields in, but not finished); it can never upgrade
   one. A progress bar you can advance by clicking tells you nothing.
3. **CONTAIN auto-skips** when the impact is genuinely bounded — S3/S4, one tenant, not growing —
   with the reason shown. Forcing containment on a ticket with nothing to contain teaches operators
   to click past stages.
4. **CONFIRM is a gate, not a workspace.** Three verdicts and nothing else: CONFIRMED → FIX,
   REFUTED → back to NARROW, INCONCLUSIVE → improve the test. It passes only if the cause explains
   the IS *and* the IS NOT **and** toggles the symptom on demand.

### Next best action

One recommendation sits under the pipeline bar at all times. Shortcuts fire **before** any scoring —
the cheapest test is the one you never run:

| Situation | Recommendation |
|---|---|
| Live duplicate open | *Do not work this ticket — join INC-xxxx* |
| Works as designed | *Do not troubleshoot — send the documented behaviour* |
| Known error with a verified fix | *Do not re-derive — apply KB-xxxx to ONE target* |
| Nothing known | The pending test that maximises **P(this ends it) / cost** |

The last row is the inversion that matters: **rank tests, not causes.** A test is chosen for its
chance of ending the investigation per minute spent, and an irreversible test is never recommended
while a reversible one exists — a reversible 20% test beats an irreversible 90% one.

### The narrowing loop log

One row per pass around NARROW → TEST → CONFIRM: *distinction tested · variable changed · expected ·
actual · verdict · next*. This is what makes the funnel auditable and what the handover and the KB
article are written from. A paragraph of freeform notes cannot answer *"what have we already tried,
and what did it rule out?"* — the free-text lane is still there beside it for everything the table
cannot hold.

### Four governing rules

1. **Impact before analysis.** If people, customers, safety, revenue or data are being harmed,
   contain first — but preserve enough evidence to investigate afterwards.
2. **Mitigation is not root cause.** A restart, rollback, failover or workaround may restore service
   without explaining anything. Keep workaround, corrective action and root cause separate.
3. **Troubleshooting is progressive isolation.** *All customers? No — only Europe. All European
   systems? No — cluster B. All cluster B nodes? No — newly provisioned.* Keep dividing.
4. **A cause must explain the IS *and* the IS NOT** — why here and not there, why then, why that
   extent. This is the falsification step that "5 Whys" has no equivalent for.

### Universal Action Record

A live 20-field one-page summary — issue, expected state, impact, severity, blast radius, mitigation,
IS, IS NOT, distinctions, changes, hypotheses, evidence for and against, next discriminating test,
most probable cause, verification, corrective action, prevention, owner and status. Every row is
**derived** from the sections above, so it can never drift out of step with the ticket.

---

## What you get

### AI agent — inference on every step

**Triage runs automatically** the moment a ticket opens, and answers four questions outright:

| Question | Answer |
|---|---|
| Same issue already open? | **YES — 2 open** · ticket number, status, severity, age |
| Seen before? | **YES — 8 solved previously** · what each one cost |
| Working as designed? | **No — genuine fault** (or *YES — no defect*, don't troubleshoot) |
| Known fix available? | **YES — KB-2025-0001** · match score and reuse count |

**Plan the pipeline** fills the 8 stages from this ticket, the KB and the customer's history — and
crucially **marks what is already answered**. A known error marks **NARROW and TEST** already done —
the expensive middle of any investigation — and sends you straight to CONFIRM. A works-as-designed
verdict marks NARROW, TEST and CONFIRM *n/a*. PRIORITIZE is never skipped, however well known the cause.

Also: 3 ranked probable causes with evidence for and against, plan critique, root-cause inference,
KB-article drafting, escalation handover drafting, Problem clustering, and a fleet health diagnostic.

Every plan is **costed against what the issue took the first time** — the demo's headline recurrence
drops from **215 minutes to a 40-minute plan**.

Two rules the agent never breaks: **it proposes, you decide** — nothing changes ticket state on its
own; and every claim **shows its evidence**, with expandable reasoning.

### Smart Knowledge Base — memory that compounds

One schema for every article, so a retrieval agent can search it.
**Error-signature normalization** strips timestamps, UUIDs, request IDs and host IPs while *keeping*
meaningful constants, so the same fault matches twice. **Explainable search** shows a match score and
*why* it matched. **Automatic de-duplication** bumps an article's reuse count instead of creating a
near-twin. **Secret scrubbing** redacts passwords, keys, tokens, SSH targets and emails before
publishing — and blocks publishing if you switch it off. **Verified-fix gating** stops an unverified
article outranking a proven one. Searches return **the top 3 findings**, not a wall of results.

### Problem & recurrence management

**Problem records outlive the ticket** and own the root cause; incidents merely reference them.
**Recurrence counting** is the evidence that funds a permanent fix. **Known Error** state for a
verified workaround with no permanent fix yet. **Blast radius and cost** — every affected customer
and the hours spent across all linked cases. **Auto-clustering** proposes unlinked cases sharing a
signature as one Problem. A **coded root-cause taxonomy**, so *"what breaks us most often"* becomes a
chart rather than an opinion.

### Two views of one system *(v9.2)*

The page opens with a view switch: **Customer ticket view** and **Support ticket view**.

The customer view is a guided six-step intake — contact & scope, what is wrong, where & when,
**what still works**, evidence & history, access & submit — generated from the field list in
`kt_intake.js`. Those field ids *are* the support form's element ids, so a submission loads into the
KT form with no mapping table and no drift.

Submitting raises a real ticket. It is registered in the case store immediately, so it appears in the
**operations dashboard, Customer 360, the topology mind map and the ticket history** the moment it is
sent — and lands in a **Customer intake** inbox beside the demo-ticket selector, where one click loads
it into the KT form with triage already run.

Three things the customer view deliberately does **not** do:

- **It never reaches the AI agent.** Triage returns other tenants' open ticket ids — on this demo,
  an HN-Bank ticket's triage names an HCMC-Commerce case. That belongs to support, not to a customer.
- **It never sets severity.** The customer describes impact and blast radius; support grades it. A
  submission arrives as S3 with the customer's own words attached.
- **It shows only three KB fields** — title, description and workaround. Root cause, resolution steps,
  article ids and reuse counts stay internal: they name hosts, config files and shell commands.

A **ticket quality** meter scores the six things that change how the ticket gets worked, each stated as
what it *unlocks* rather than as a number to game — the exact error message, a comparable case that
works, what changed beforehand, steps to reproduce, when it started, and what it blocks. "What still
works" is a step of its own because a cause has to explain why the healthy twin is healthy, and an
intake form that never asks produces a ticket support cannot start on.

While they type, the portal checks their signature against the KB and against **their own** prior
cases — never another tenant's — so a known issue can be answered before the ticket is even raised.

### Customer topology mind map *(v9)*

Pick a customer on the ticket form and §0.1 draws every **open** ticket that customer has as a
**mermaid mind map**, four levels deep:

```
customer  →  site  →  infra location  →  issue
```

The infra location is the level a ticket tool normally loses. A case carries a site (`HN`) and a
component (`ceph`) — neither tells you *where you would walk*. v9 maps component → infra class →
the real locations of that class at that site (`HN-CEPH-SSD · 36 OSD · NVMe tier`,
`HN-POD-A · racks A1-A6 · 48 hypervisors`), and the ticket id picks one deterministically, so a
ticket lands in the same place on every reload without storing a new field.

- **Mind map** view (mermaid `mindmap`) — the structure at a glance; branch colour marks the site.
- **Tree** view (mermaid `flowchart`) — the same hierarchy coloured by severity, with every ticket
  node **clickable** through to its case timeline.
- The ticket **you are currently typing** appears on the map before it has ever been saved,
  highlighted as *this ticket* — so you see immediately whether you are about to open the fourth
  ticket on the same pod.
- Chips call out the hotspot location and any S1s; *Open only* can be switched off to see all history.
- Zoom, expand to full screen, download the SVG, or copy the **mermaid source** into any wiki that
  renders mermaid.

Mermaid is inlined into the standalone bundle, so this still works with **zero external requests**.

### Customer ticket history *(v9)*

**Ticket history** beside the customer selector opens the windowed account view: **last 3 days /
last week / last month / any custom date range**. Tickets opened per day (S1s stacked in red),
opened / still open / closed / MTTR against the customer's SLA / S1 count / recurring Problems,
breakdowns by component, site and coded root cause, and the full ticket table with each ticket's
**infra location** resolved. **Export CSV** for the account review.

The window is anchored on when a ticket was *opened*, not when it closed — so "last week" means the
week's intake, which is the number an account review actually argues about.

### Customer & fleet intelligence

**Customer 360** — case history, MTTR trend, recurring issues, open Problems, top failing components.
**Related cases** across four relation types. **Case timeline** as the handover artefact.
**Operations dashboard** — 1 day / 3 days / 1 week / 30 days, opened vs solved, pending, S1s, MTTR,
top 10 customers / issue types / infrastructure, and an **infrastructure health score** with the
factors costing points and a prioritised remediation plan.

---

## Conventions

**Severity, not priority** — severity describes *what is broken*, not how fast we answer:

| | |
|---|---|
| **S1** | Full system down |
| **S2** | Features not working |
| **S3** | Performance issues |

**Blast radius** is a controlled facet — *All users* / *Internal users* / *Specific user*. Naming who
IS affected implies who IS NOT, which is where the KT specification starts.

**Ticket numbers** are generated as `CUSTOMER-LOCATION-NUMBER` — e.g. `HNB-HN-0042`. Changing the
customer or site rebuilds the prefix around the same sequence number rather than burning a new one.

**Section colour = ownership.** Red sections are customer input, blue are support input, green is
closure. Search sections (§9.3) sit **before** the action plan (§10), because you search before you
troubleshoot.

---

## How it simplifies the work

| Question an engineer asks | Traditional ticket tool | KATS |
|---|---|---|
| Is this already open on another ticket? | Search manually, if you think to | **Answered on open** — ticket number, status, severity, age |
| Have we solved this before? | Ask a colleague, trawl closed tickets | **Ranked matches** with the verified fix and what it cost last time |
| Is this even a fault? | Troubleshoot first, find out later | **Works-as-designed detected** — explain and close |
| What should I try first? | Engineer's judgement, unrecorded | **3 ranked causes**, each with evidence and one single-variable test |
| What breaks us most often? | Not answerable | **Pareto by coded root-cause category** |
| What does this customer keep hitting? | Read the ticket list | **Customer 360** — MTTR trend, recurring issues, open Problems |
| Where is this customer bleeding right now? | Filter the queue and read row by row | **Topology mind map** — every open ticket by site and infra location, hotspot called out |
| What did this account see last week? | Build a query, export, pivot | **Ticket history** — 3 days / week / month / custom range, with CSV |
| How do I hand this over? | Write it out again from the comment thread | **Handover drafted** from the tests you already ran |

The design goal is **fewest actions and fewest minutes to a correct answer** — not more forms.

---

## Who it's for

| Role | What they get |
|---|---|
| **L1 / Service Desk** | Told immediately whether to escalate, apply a known fix, or close as working-as-designed |
| **L2 / L3 engineers** | A ranked hypothesis list with evidence, instead of a blank page at 2am |
| **Team leads** | Recurrence counts and cost-per-Problem — the argument for funding a permanent fix |
| **Service delivery managers** | Customer 360: MTTR trend, open Problems, what this account keeps hitting |
| **Operations / SRE** | Fleet dashboard with an infrastructure health score and prioritised remediation |
| **New joiners** | The method is built into the form, so the structure teaches the method |

---

## What's in the box

| File | What it is |
|---|---|
| `rax_ticket_support_page.html` | **Start here.** Chooser — legacy view vs KATS, side by side |
| `kt_support_demo_v9.html` | **The demo.** Single standalone file, 0 external requests (4.7 MB — mermaid is inlined) |
| `kt_support_demo.html` | The v8 demo, kept for comparison (1.5 MB, no topology map) |
| `core_ticket_rebuilt.html` | A legacy ticket view, rebuilt — the "before" side of the story |
| `kt_support_v9.html` | Source of the demo (needs the JS modules beside it) |
| `kt_support_v8.html` | Source of the previous release |
| `kb_database.js` | KB schema, 12 seeded articles, search, dedupe, secret scrubbing |
| `kt_data.js` | Customers, Cases, Problems (the ITIL spine) + dashboard analytics |
| `kt_topology.js` | **v9.** Infra topology (customer → site → infra location), mermaid emitters, windowed ticket history |
| `kt_pipeline.js` | **v9.1.** The 8 stage definitions, computed progress, the loop model, the next-best-action rule, and the 10→8 migration |
| `kt_intake.js` | **v9.2.** The customer intake contract — field list, ticket quality model, customer-safe KB lookup, and the queue the support view reads |
| `ai_agent.js` | The AI agent layer — **mock**, with the real contract, and the shared 10-step plan |
| `demo_tickets.js` | 10 fully-populated demo tickets, each with a related case and a filled plan |
| `build_demo.js` | Bundles everything into the single standalone file (`node build_demo.js v9\|v8`) |
| `test_topology.js` | `node test_topology.js` — dependency-free checks on the topology + history layer |
| `test_pipeline.js` | `node test_pipeline.js` — dependency-free checks on the pipeline, the migration and the next-best-action rule |
| `start.sh` | Serves it and opens a browser |
| `vendor/` | bootstrap, font-awesome and mermaid, downloaded once so the build needs no network |

**Demo data:** 5 customers · 4 sites · 24 infra locations · 100+ cases · 6 Problem records ·
12 KB articles · 10 demo tickets with
**all 10 action-plan steps filled** (84 done, 7 not started, 6 in progress, 3 n/a), covering every
triage verdict — known error, recurrence, works-as-designed, new investigation. Recent activity is
synthesised **relative to today**, so the dashboard never goes stale.

### Rebuilding after an edit

```bash
node build_demo.js       # re-inlines everything into kt_support_demo_v9.html
node build_demo.js v8    # rebuild the previous release instead
./start.sh --build       # rebuild, then serve
```

The build **fails loudly** if any external URL or unresolved local reference survives, so a broken
bundle can't ship silently.

```bash
node test_topology.js   # topology + history checks, no dependencies
node test_pipeline.js   # 8-stage pipeline, 10→8 migration, next-best-action
```

---

## Is the AI real?

**No. It is a mock, and the code says so.** Every result is produced by deterministic reasoning over
the knowledge base, case store and problem register — with simulated latency so it behaves like an
agent on stage. Every panel is labelled `model: kt-support-agent (mock, no model called)`.

What *is* real is the **contract**:

```js
AIAgent.run(task, context) -> Promise<AIResult>
```

Swap a single function — `AIAgent._infer()` — for a real LLM call, or set
`AIAgent.configure({ mode: 'api', endpoint })`, and all eight agent tasks keep working, because the
UI only ever consumes the `AIResult` shape.

The eight tasks: `triage`, `action_plan`, `probable_causes`, `critique_plan`, `root_cause`,
`kb_draft`, `handover`, `cluster`, `infra_health`.

### Making it genuinely intelligent

Retrieval today is lexical scoring, so *"VM won't boot"* and *"instance fails to spawn"* score as
unrelated. Every article already carries an assembled `embedding_text` field for exactly this reason —
blend vector similarity into `KB.score()` and all the agent's matching features become semantic at
once, with no UI changes.

---

## Data and privacy

- The demo ships **fictional data only** — invented customers, tickets and infrastructure.
- Section 8 (Access & Constraints) is **scrubbed for secrets** before anything reaches the KB:
  passwords, private keys, tokens, SSH targets and emails are detected and redacted.
- Publishing is **blocked** if secrets are detected and scrubbing is switched off.
- State lives in `localStorage`, per browser. Nothing is transmitted anywhere unless you configure a
  KB API endpoint yourself.

---

## Status and limitations

This is a **proof of concept**, honestly labelled:

- The AI agent is mocked (see above).
- Storage is per-browser `localStorage` — fine for one operator, not for a team. Multi-user needs the
  KB/case API endpoints wired to a real backend; the POST paths exist and are tested.
- Search is lexical, not semantic.
- The dashboard's recent activity is synthesised relative to today, so the demo never goes stale.
- The UI field is `severity`, but stored case and KB records still use `priority` as the schema key —
  renaming it across 100+ seeded records was not worth the risk for a PoC.
- The **infra location** on the topology map is *derived*, not authoritative: a case stores a site and
  a component, and `kt_topology.js` resolves those to a location from a hard-coded site inventory.
  It is deterministic and stable, but it is a stand-in for a CMDB lookup. Wiring it to a real
  inventory means replacing `Topology.locate()` — nothing above it changes.

## License

Proof of concept, shared for evaluation. No warranty.
