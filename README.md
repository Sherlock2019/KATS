# KATS — KT AI Enhanced Ticket Support System

A support ticket workflow built on the **Kepner-Tregoe** problem-analysis method, with an AI agent
that reads your knowledge base, customer history and problem register to find the **shortest path to
resolution** — including telling you not to troubleshoot at all.

> **Proof of concept.** Runs entirely in a browser, offline, from a single file.
> The AI agent is a **mock** — no model is called. See [Is the AI real?](#is-the-ai-real).

---

## Try it in 30 seconds

```bash
git clone git@github.com:Sherlock2019/KATS.git
cd KATS
./start.sh
```

Or just open `kt_support_demo.html` in a browser. No install, no build, no server, no internet.

---

## The problem it solves

Every ticket tool on the market records **what happened to the ticket** — who it was assigned to,
which queue it moved through, when the status changed.

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

---

## How it simplifies the work

| Question an engineer asks | Traditional ticket tool | KATS |
|---|---|---|
| Is this already open on another ticket? | Search manually, if you think to | **Answered on open** — ticket number, status, priority, age |
| Have we solved this before? | Ask a colleague, trawl closed tickets | **Ranked matches** with the verified fix and what it cost last time |
| Is this even a fault? | Troubleshoot first, find out later | **Works-as-designed detected** — explain and close |
| What should I try first? | Engineer's judgement, unrecorded | **3 ranked causes**, each with evidence for/against and one single-variable test |
| What breaks us most often? | Not answerable | **Pareto by coded root-cause category** |
| What does this customer keep hitting? | Read the ticket list | **Customer 360** — MTTR trend, recurring issues, open Problems |
| How do I hand this over? | Write it out again from the comment thread | **Handover drafted** from the tests you already ran |

The whole design goal is **fewest actions and fewest minutes to a correct answer** — not more forms.

---

## Benefits

- **Skip discovery when the answer already exists.** Triage runs the moment a ticket opens and says
  whether this is a known error, a recurrence, a duplicate, or genuinely new.
- **Stop work that shouldn't happen.** A *works-as-designed* verdict ends the ticket in minutes
  instead of hours.
- **Catch duplicates before two people work them.** Same normalized error signature = flagged, with
  the other ticket's number and current status.
- **Make recurrence visible.** A Problem record outlives the ticket and counts how often it comes
  back — the evidence that funds a permanent fix.
- **Make root causes countable.** A coded taxonomy instead of free text, so *"what breaks us"*
  becomes a chart rather than an opinion.
- **Keep knowledge when people leave.** Every closed case can become a verified KB article, in one
  schema, ready for a retrieval agent.

In the bundled demo data, a recurrence that originally took **215 minutes** to solve is resolved
with a **40-minute** plan — because the fix was already known.

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
| `rax_ticket_support_page.html` | **Start here.** Landing page — choose legacy view or KATS |
| `kt_support_demo.html` | **The demo.** Single standalone file, 0 external requests |
| `core_ticket_rebuilt.html` | A legacy ticket view, rebuilt — the "before" side of the story |
| `kt_support_v8.html` | Source of the demo (needs the JS modules beside it) |
| `kb_database.js` | Knowledge base: schema, 12 seeded articles, search, dedupe, secret scrubbing |
| `kt_data.js` | Customers, Cases, Problems (the ITIL spine) + dashboard analytics |
| `ai_agent.js` | The AI agent layer — **mock**, with the real contract |
| `demo_tickets.js` | 10 fully-populated demo tickets, each with a related case |
| `build_demo.js` | Bundles everything into the single standalone file |
| `start.sh` | Serves it and opens a browser |

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

**Two design rules the agent never breaks:**

- It **proposes; you accept or reject.** Nothing changes ticket state on its own.
- Every claim **shows its evidence** — KB article, Problem or case IDs, plus expandable reasoning.

### Making it genuinely intelligent

The retrieval today is lexical scoring, so *"VM won't boot"* and *"instance fails to spawn"* score as
unrelated. Every article already carries an assembled `embedding_text` field for exactly this reason —
blend vector similarity into `KB.score()` and all the agent's matching features become semantic at
once, with no UI changes.

---

## Data and privacy

- The demo ships **fictional data only** — invented customers, tickets and infrastructure.
- Section 8 (Access & Constraints) is **scrubbed for secrets** before anything is published to the KB:
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
- The dashboard's recent activity is **synthesised relative to today**, so the demo never goes stale.

## License

Proof of concept, shared for evaluation. No warranty.
