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
./start.sh kats     # straight to KATS
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

## The Universal Troubleshooting Action Plan

Domain-neutral — it works for IT, cloud, AI, infrastructure, engineering, manufacturing, operations
and business processes:

**Protect → Prioritize → Mitigate → Define → Isolate → Hypothesize → Eliminate → Verify → Correct → Prevent**

| # | Phase | Step | Core question |
|---|---|---|---|
| 1 | PROTECT | Assess impact and protect evidence | What is the impact, and what evidence must we preserve? |
| 2 | PRIORITIZE | Prioritize and stabilize | What must we protect or restore first? |
| 3 | MITIGATE | Find a safe workaround or mitigation | How can we reduce the damage now without hiding the cause? |
| 4 | DEFINE | State and bound the deviation | What exactly is wrong, and what should be happening instead? |
| 5 | SPECIFY | Specify the problem — IS / IS NOT | Where does it occur, and where could it occur but does not? |
| 6 | ISOLATE | Isolate through distinctions and changes | What is different, and what changed around that difference? |
| 7 | HYPOTHESIZE | Generate possible causes | What mechanisms could produce exactly this pattern? |
| 8 | ELIMINATE | Test, eliminate and rank causes | Which causes explain all the evidence with fewest assumptions? |
| 9 | VERIFY | Verify the true cause | Can changing this factor predictably make it appear or disappear? |
| 10 | CORRECT & PREVENT | Correct, confirm and prevent recurrence | Did we really fix it, why was it possible, and how do we stop it? |

Every step carries its status, owner and result notes, and is exported with the ticket.

### The funnel

```
IMPACT → PRIORITIZE → MITIGATE → DEFINE → IS / IS NOT → ISOLATE →
DISTINCTIONS + CHANGES → POSSIBLE CAUSES → ELIMINATE → MOST PROBABLE CAUSE →
VERIFY → ROOT CAUSE → CORRECT → PREVENT
```

The objective is not an ever-growing list of possibilities. It is to **continuously reduce the search
space until only a defensible cause remains**. Two funnels run in parallel — operational
(max impact → min impact) and diagnostic (max uncertainty → min uncertainty).

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

**Propose action plan** fills the 10 steps from this ticket, the KB and the customer's history — and
crucially **marks what is already answered**. A known error skips steps 6–8 (isolate → hypothesize →
eliminate), the expensive middle of any investigation. A works-as-designed verdict marks them *n/a*.

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
| `kt_support_demo.html` | **The demo.** Single standalone file, 0 external requests |
| `core_ticket_rebuilt.html` | A legacy ticket view, rebuilt — the "before" side of the story |
| `kt_support_v8.html` | Source of the demo (needs the JS modules beside it) |
| `kb_database.js` | KB schema, 12 seeded articles, search, dedupe, secret scrubbing |
| `kt_data.js` | Customers, Cases, Problems (the ITIL spine) + dashboard analytics |
| `ai_agent.js` | The AI agent layer — **mock**, with the real contract, and the shared 10-step plan |
| `demo_tickets.js` | 10 fully-populated demo tickets, each with a related case and a filled plan |
| `build_demo.js` | Bundles everything into the single standalone file |
| `start.sh` | Serves it and opens a browser |

**Demo data:** 5 customers · 100+ cases · 6 Problem records · 12 KB articles · 10 demo tickets with
**all 10 action-plan steps filled** (84 done, 7 not started, 6 in progress, 3 n/a), covering every
triage verdict — known error, recurrence, works-as-designed, new investigation. Recent activity is
synthesised **relative to today**, so the dashboard never goes stale.

### Rebuilding after an edit

```bash
node build_demo.js      # re-inlines everything into kt_support_demo.html
./start.sh --build      # rebuild, then serve
```

The build **fails loudly** if any external URL or unresolved local reference survives, so a broken
bundle can't ship silently.

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

## License

Proof of concept, shared for evaluation. No warranty.
