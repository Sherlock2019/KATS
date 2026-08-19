# KATS — KT AI Enhanced Ticket Support System

A support ticket workflow built on the **Kepner-Tregoe** problem-analysis method, with an AI agent
that reads your knowledge base, customer history and problem register to find the **shortest path to
resolution** — including telling you not to troubleshoot at all.

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

## What you get

### 1. Kepner-Tregoe problem analysis

Structured intake — **WHAT, WHERE, WHEN, TREND, HOW MANY** — instead of a free-text description.
**IS / IS NOT** bounding, because the cause must explain both what is broken *and* what is comparable
but healthy. **A/B distinction** against a working twin. **Single-variable testing**, so a result
actually proves something. A **5-Whys causal chain** with evidence required at every link.

### 2. AI agent — inference on every step

**Triage runs automatically** the moment a ticket opens, and answers four questions outright:

| Question | Answer |
|---|---|
| Same issue already open? | **YES — 2 open** · ticket number, status, priority, age |
| Seen before? | **YES — 8 solved previously** · what each one cost |
| Working as designed? | **No — genuine fault** (or *YES — no defect*, don't troubleshoot) |
| Known fix available? | **YES — KB-2025-0001** · match score and reuse count |

Then: **3 ranked probable causes**, each with evidence for, evidence against, one single-variable
test and an expected result. Plus plan critique, root-cause inference, KB-article drafting,
escalation handover drafting, Problem clustering, and a fleet health diagnostic.

Every plan is **costed against what the issue took the first time** — the demo's headline recurrence
drops from **215 minutes to a 40-minute plan**.

Two rules the agent never breaks: **it proposes, you decide** — nothing changes ticket state on its
own; and every claim **shows its evidence**, with expandable reasoning.

### 3. Smart Knowledge Base — memory that compounds

One schema for every article, so a retrieval agent can actually search it.
**Error-signature normalization** strips timestamps, UUIDs, request IDs and host IPs while *keeping*
meaningful constants, so the same fault matches twice. **Explainable search** shows a match score and
*why* it matched. **Automatic de-duplication** bumps an article's reuse count instead of creating a
near-twin. **Secret scrubbing** redacts passwords, keys, tokens, SSH targets and emails before
publishing — and blocks publishing if you switch it off. **Verified-fix gating** stops an unverified
article outranking a proven one.

### 4. Problem & recurrence management

**Problem records outlive the ticket** and own the root cause; incidents merely reference them.
**Recurrence counting** is the evidence that funds a permanent fix. **Known Error** state for a
verified workaround with no permanent fix yet. **Blast radius and cost** — every affected customer
and the hours spent across all linked cases. **Auto-clustering** proposes unlinked cases sharing a
signature as one Problem. A **coded root-cause taxonomy**, so *"what breaks us most often"* becomes a
chart rather than an opinion.

### 5. Customer & fleet intelligence

**Customer 360** — case history, MTTR trend, recurring issues, open Problems, top failing components.
**Related cases** across four relation types. **Case timeline** as the handover artefact.
**Operations dashboard** — 1 day / 3 days / 1 week / 30 days, opened vs solved, pending, P1s, MTTR,
top 10 customers / issue types / infrastructure, and an **infrastructure health score** with the
factors costing points and a prioritised remediation plan.

<details>
<summary><b>Full capability list (40 items)</b></summary>

**Kepner-Tregoe** — structured WHAT/WHERE/WHEN/TREND/HOW MANY intake · IS / IS NOT bounding ·
A/B distinction against a working twin · single-variable testing · 5-Whys causal chain with evidence ·
impacted nodes and VMs listed individually, healthy ones marked as the IS NOT set

**AI agent** — automatic triage · duplicate & recurrence detection with ticket number, status,
priority and age · works-as-designed verdict · 3 ranked probable causes with tests · plan review that
shortens your plan · root-cause inference with coded category and causal chain · handover drafting ·
KB-article drafting · fleet health diagnostic · time saved quantified · proposes-you-decide with
evidence shown

**Smart KB** — one schema · error-signature normalization · explainable search · automatic
de-duplication · secret scrubbing with publish blocking · verified-fix gating · reuse tracking ·
vector-search ready with assembled embedding field and JSON export

**Problems** — records outlive the ticket · recurrence counting · Known Error state · blast radius
and cost · auto-clustering · coded root-cause taxonomy

**Intelligence** — Customer 360 · related cases (4 relation types) · case timeline · operations
dashboard with 5 time ranges · top 10 customers / issue types / infrastructure · infrastructure
health score

**Workflow** — colour-coded ownership (red = customer input, blue = support input) · KB-readiness
meter · comments on every step · autosave and draft restore · full ticket export as JSON

</details>

---

## How it simplifies the work

| Question an engineer asks | Traditional ticket tool | KATS |
|---|---|---|
| Is this already open on another ticket? | Search manually, if you think to | **Answered on open** — ticket number, status, priority, age |
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
| **New joiners** | The KT method is built into the form, so the structure teaches the method |

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
| `ai_agent.js` | The AI agent layer — **mock**, with the real contract |
| `demo_tickets.js` | 10 fully-populated demo tickets, each with a related case |
| `build_demo.js` | Bundles everything into the single standalone file |
| `start.sh` | Serves it and opens a browser |

**Demo data:** 5 customers · 100+ cases · 6 Problem records · 12 KB articles · 10 demo tickets
covering every triage verdict (known error, recurrence, works-as-designed, new investigation).
Recent activity is synthesised **relative to today**, so the dashboard never goes stale.

### Rebuilding after an edit

```bash
node build_demo.js      # re-inlines everything into kt_support_demo.html
./start.sh --build      # rebuild, then serve
```

The build **fails loudly** if any external URL or unresolved local reference survives, so a broken
bundle can't ship silently.

---

## The method, in four steps

KATS is a shell around Kepner-Tregoe Problem Analysis. Its central idea: a problem is a **deviation
from expected performance**, and the cause must explain **both** what IS happening and what IS NOT.

1. **Describe the deviation** — one sentence for what should happen, one for what does. No theories.
2. **Bound it** — WHERE, WHEN, TREND, HOW MANY. For each, record what is affected *and* what is
   comparable but healthy.
3. **Find the distinction** — put the broken case beside a working twin. The shortest list of
   differences is your candidate cause list.
4. **Test one variable** — reversibly, on one target. If the symptom toggles with it, that's your
   root cause. Change two things and you've proven nothing.

That second half of step 1 is what most teams skip, and it's why KT beats "5 Whys" on real incidents:
5 Whys assumes a single linear chain and never tests alternatives, so it walks you confidently to a
plausible but wrong answer.

Further reading: [Beyond 5 Whys — Kepner-Tregoe](https://kepner-tregoe.com/blogs/beyond-5-whys-problem-solving-skills-for-real-life/)

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

The eight tasks: `triage`, `probable_causes`, `critique_plan`, `root_cause`, `kb_draft`, `handover`,
`cluster`, `infra_health`.

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

## License

Proof of concept, shared for evaluation. No warranty.
