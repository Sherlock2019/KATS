/* =============================================================================
 * ai_agent.js — AI Agent layer (MOCK / PoC)
 *
 * ⚠ THIS IS A MOCK. No model is called. Every result is produced by
 * deterministic reasoning over KB_DATABASE + CASE_STORE + PROBLEM_STORE,
 * with simulated latency so the demo behaves like a real agent.
 *
 * The point of the mock is the CONTRACT. Swap one function —
 * AIAgent._infer() — for a real LLM call and every button in the UI keeps
 * working, because they all consume the same AIResult shape.
 *
 *   AIAgent.run(task, ctx) -> Promise<AIResult>
 *
 *   AIResult = {
 *     task, verdict, headline, confidence,
 *     evidence : [{ type:'kb'|'case'|'problem', id, label, why }],
 *     actions  : [{ seq, action, why, expected, risk, est_mins, source }],
 *     changes  : [{ op:'add'|'remove'|'reorder'|'edit', target, detail, why }],
 *     fields   : { ...form fields the agent proposes to fill },
 *     reasoning: [ '...' ],
 *     est_total_mins, baseline_mins, est_saving_mins,
 *     model, generated_at, disclaimer
 *   }
 *
 * Agent objective (as briefed): reach resolution in the MINIMUM number of
 * actions and minutes, using history + similar issues + known solutions —
 * including concluding "works as designed" when that is the correct answer.
 * ========================================================================== */
(function (global) {
  'use strict';

  const CONFIG = {
    mode: 'mock',                 // 'mock' | 'api'
    endpoint: '',                 // POST target when mode === 'api'
    model: 'kt-support-agent (mock, no model called)',
    latencyMs: [600, 1100]
  };

  const TASKS = {
    triage: 'Triage — is this known, a duplicate, a recurrence, or working as designed?',
    probable_causes: 'Rank 3 probable causes with evidence and a single-variable test each',
    critique_plan: 'Review the engineer\'s plan and suggest changes',
    root_cause: 'Propose a root cause + category from the evidence',
    kb_draft: 'Draft the KB article from the solved ticket',
    handover: 'Draft the escalation handover',
    cluster: 'Cluster unlinked cases into Problem records',
    infra_health: 'Diagnose fleet-wide infrastructure health'
  };

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const rnd = () => CONFIG.latencyMs[0] + Math.floor(Math.random() * (CONFIG.latencyMs[1] - CONFIG.latencyMs[0]));

  function base(task) {
    return {
      task,
      task_label: TASKS[task] || task,
      verdict: null, headline: '', confidence: 0.5,
      evidence: [], actions: [], changes: [], fields: {}, reasoning: [],
      est_total_mins: null, baseline_mins: null, baseline_label: '', est_saving_mins: null,
      model: CONFIG.model,
      generated_at: new Date().toISOString(),
      disclaimer: 'AI-generated suggestion. Review before acting — the agent never changes ticket state on its own.'
    };
  }

  /** Split a KB resolution ("1) ... 2) ...") into discrete actions. */
  function stepsFromResolution(text) {
    if (!text) return [];
    const parts = String(text).split(/\n?\s*\d+\)\s*/).map(s => s.trim()).filter(Boolean);
    return parts.length > 1 ? parts : [String(text).trim()];
  }

  function kbById(id) { return (global.KB_DATABASE || []).find(a => a.kb_id === id) || null; }

  /* =====================================================================
   * TASK: triage
   * The highest-value call. Decides the fastest legitimate path to done.
   * ================================================================== */
  function doTriage(ctx) {
    const r = base('triage');
    const norm = (global.KB && ctx.error_signature_raw)
      ? global.KB.normalizeErrorSignature(ctx.error_signature_raw) : '';

    const hits = (global.KB && ctx.query)
      ? global.KB.search(ctx.query, {
          errorSignature: ctx.error_signature_raw,
          facets: { service_component: ctx.service_component, environment: ctx.environment, site: ctx.site },
          limit: 3, minScore: 1
        }) : [];

    const problem = global.Problems ? global.Problems.forSignature(norm, ctx.service_component) : null;
    const related = global.Cases ? global.Cases.related(
      { ticket_id: ctx.ticket_id, customer_id: ctx.customer_id, service_component: ctx.service_component, error_signature_raw: ctx.error_signature_raw }, 8) : [];
    const sameCustomerSameIssue = related.filter(x =>
      x.relation === 'same-signature' && x.case.customer_id === ctx.customer_id);

    const top = hits[0];
    // Baseline = what this cost the first time it was seen (before the KB
    // article existed). Falls back to the component median for novel issues.
    r.baseline_mins = null;
    r.baseline_label = '';
    if (problem && global.Cases) {
      r.baseline_mins = global.Cases.firstTimeMinutes(problem.problem_id);
      r.baseline_label = 'first-time resolution of ' + problem.problem_id + ' (before KB)';
    }
    if (r.baseline_mins == null && global.Cases) {
      r.baseline_mins = global.Cases.baselineMinutes(ctx.service_component);
      r.baseline_label = 'historical median for ' + (ctx.service_component || 'this component');
    }

    /* ---- Direct answers to the three questions triage exists to settle ----
       1. Is the same issue open RIGHT NOW on another ticket?
       2. Have we seen it before, and was it solved?
       3. Is it a defect at all, or working as designed?
       These are surfaced explicitly rather than left to be inferred from
       evidence chips. */
    // Query the case store DIRECTLY rather than reusing `related` — that list
    // is capped for display, and closed history would crowd out the open
    // duplicate, which is the single most important thing triage can report.
    const sameSig = (global.CASE_STORE || []).filter(c =>
      c.ticket_id !== ctx.ticket_id && norm && c.error_signature_norm &&
      (c.error_signature_norm === norm ||
       c.error_signature_norm.includes(norm) || norm.includes(c.error_signature_norm)));

    const newestFirst = (a, b) => String(b.opened_at).localeCompare(String(a.opened_at));
    const openDupes = sameSig.filter(c => c.status !== 'Closed').sort(newestFirst);
    const closedDupes = sameSig.filter(c => c.status === 'Closed').sort(newestFirst);

    const ago = when => {
      const t = new Date(String(when).replace(' ', 'T')).getTime();
      if (isNaN(t)) return '';
      const h = Math.round((Date.now() - t) / 3600000);
      return h < 1 ? 'just now' : h < 48 ? h + 'h ago' : Math.round(h / 24) + 'd ago';
    };
    const custName = id => (global.Customers && global.Customers.byId(id))
      ? global.Customers.byId(id).name : id;

    r.duplicates_open = openDupes.map(c => ({
      ticket_id: c.ticket_id, status: c.status,
      customer: custName(c.customer_id), opened: c.opened_at,
      age: ago(c.opened_at), priority: c.priority
    }));
    r.duplicates_closed = closedDupes.map(c => ({
      ticket_id: c.ticket_id, status: 'Closed',
      customer: custName(c.customer_id), opened: c.opened_at,
      age: ago(c.opened_at), ttr: c.time_to_resolve_mins
    }));

    const isWad = (problem && problem.status === 'works-as-designed') ||
                  (top && top.article.tags && top.article.tags.includes('works-as-designed'));

    r.answers = [
      {
        q: 'Same issue already open?',
        a: openDupes.length ? 'YES — ' + openDupes.length + ' open' : 'No',
        tone: openDupes.length ? 'warn' : 'ok',
        detail: openDupes.length
          ? r.duplicates_open.slice(0, 4).map(d => d.ticket_id + ' · ' + d.status + ' · P' + d.priority +
              ' · ' + d.customer + ' · opened ' + d.age).join('\n') +
            (openDupes.length > 4 ? '\n+ ' + (openDupes.length - 4) + ' more' : '')
          : 'No other ticket is currently open with this signature.'
      },
      {
        q: 'Seen before?',
        a: closedDupes.length ? 'YES — ' + closedDupes.length + ' solved previously' : 'No prior case',
        tone: closedDupes.length ? 'info' : 'ok',
        detail: closedDupes.length
          ? r.duplicates_closed.slice(0, 3).map(d => d.ticket_id + ' · closed ' + d.age +
              (d.ttr ? ' · took ' + d.ttr + ' min' : '')).join('\n') +
            (closedDupes.length > 3 ? '\n+ ' + (closedDupes.length - 3) + ' more' : '')
          : 'First time this signature has been recorded.'
      },
      {
        q: 'Working as designed?',
        a: isWad ? 'YES — no defect' : 'No — genuine fault',
        tone: isWad ? 'ok' : 'warn',
        detail: isWad
          ? 'Matches ' + ((problem && problem.problem_id) || (top && top.article.kb_id)) +
            ', classified works-as-designed. Do not troubleshoot — explain and close.'
          : problem
            ? 'Tracked as ' + problem.problem_id + ' (' + problem.root_cause_category + '), status ' + problem.status + '.'
            : 'No works-as-designed record matches this signature.'
      },
      {
        q: 'Known fix available?',
        a: (top && top.article.verification && top.article.verification.verified)
          ? 'YES — ' + top.article.kb_id : 'No verified fix',
        tone: (top && top.article.verification && top.article.verification.verified) ? 'ok' : 'info',
        detail: top
          ? top.article.title + ' (' + Math.round(top.confidence * 100) + '% match, reused ' +
            (top.article.reuse_count || 0) + '×)'
          : 'No KB article scored above the action threshold.'
      }
    ];

    hits.forEach(h => r.evidence.push({
      type: 'kb', id: h.article.kb_id, label: h.article.title,
      why: h.reasons.join(' · ') + ' (' + Math.round(h.confidence * 100) + '%)'
    }));
    if (problem) r.evidence.push({
      type: 'problem', id: problem.problem_id, label: problem.title,
      why: problem.status + ' · seen ' + problem.recurrence_count + '× · owner ' + problem.owner
    });
    related.slice(0, 4).forEach(x => r.evidence.push({
      type: 'case', id: x.case.ticket_id, label: x.case.title, why: x.relation + ' — ' + x.why
    }));

    // --- Verdict 1: works as designed -------------------------------------
    const wad = problem && problem.status === 'works-as-designed';
    const wadKb = top && top.article.tags && top.article.tags.includes('works-as-designed');
    if (wad || wadKb) {
      const p = problem || {};
      r.verdict = 'works_as_designed';
      r.confidence = 0.88;
      r.headline = 'Works as designed — no defect. Close with an explanation, do not troubleshoot.';
      r.reasoning = [
        'The normalized signature matches ' + (p.problem_id || (top && top.article.kb_id)) + ', which is classified works-as-designed.',
        p.root_cause_statement || 'Documented expected behaviour.',
        'This pattern has been raised ' + (p.recurrence_count || 1) + '× — a documentation/expectation gap, not a fault.'
      ];
      r.actions = [
        { seq: 1, action: 'Reproduce once with correct timing/method to confirm it is the documented behaviour', why: 'Rules out a genuine fault that merely looks similar', expected: 'Behaviour matches the documented envelope', risk: 'None', est_mins: 10, source: p.problem_id || 'KB' },
        { seq: 2, action: 'Send the customer the platform behaviour reference + the observed timing', why: 'Closes the expectation gap that generated the ticket', expected: 'Customer acknowledges', risk: 'None', est_mins: 10, source: 'runbook' },
        { seq: 3, action: 'Set root cause category = works-as-designed and close', why: 'Keeps WAD volume visible in the Pareto so the doc gap gets fixed', expected: 'Case closed, no change made', risk: 'None', est_mins: 5, source: 'process' }
      ];
      r.fields = {
        permRootCause: p.root_cause_statement || 'Documented expected platform behaviour; no defect present.',
        rootCauseCategory: 'works-as-designed',
        permFix: 'No fix required. Customer given the behaviour reference and validation method.'
      };
      return finish(r);
    }

    // --- Verdict 2: recurrence of a known problem -------------------------
    if (problem && sameCustomerSameIssue.length) {
      const last = sameCustomerSameIssue.sort((a, b) => b.case.opened_at.localeCompare(a.case.opened_at))[0].case;
      r.verdict = 'recurrence';
      r.confidence = 0.92;
      r.headline = 'Recurrence of ' + problem.problem_id + ' for this customer — ' +
        sameCustomerSameIssue.length + ' prior case(s), last ' + last.opened_at.slice(0, 10) + '.';
      r.reasoning = [
        'Same normalized signature as ' + sameCustomerSameIssue.map(x => x.case.ticket_id).join(', ') + '.',
        'Problem ' + problem.problem_id + ' is ' + problem.status + '; permanent fix: ' + (problem.permanent_fix || 'not yet deployed') + '.',
        'Recurrence means the workaround holds but the permanent fix has not landed — escalate the Problem, not the incident.'
      ];
      const kb = kbById(problem.kb_id);
      r.actions = [
        { seq: 1, action: 'Apply the known workaround: ' + (problem.workaround || 'see ' + problem.kb_id), why: 'Restores service fastest; already proven ' + problem.recurrence_count + '×', expected: 'Symptom clears', risk: 'Low — reversible', est_mins: 20, source: problem.kb_id || problem.problem_id },
        { seq: 2, action: 'Link this case to ' + problem.problem_id + ' and increment recurrence', why: 'Recurrence count is the evidence that funds the permanent fix', expected: 'Problem record updated', risk: 'None', est_mins: 2, source: 'process' },
        { seq: 3, action: 'Escalate ' + problem.problem_id + ' to ' + (problem.owner || 'the Problem owner') + ' — target ' + (problem.target_fix_date || 'unset'), why: 'Third+ recurrence justifies priority on the permanent fix', expected: 'Fix date confirmed', risk: 'None', est_mins: 10, source: 'process' },
        { seq: 4, action: 'Tell the customer this is a tracked Problem with a scheduled fix', why: 'Prevents a repeat ticket next month', expected: 'Customer informed', risk: 'None', est_mins: 8, source: 'comms' }
      ];
      r.fields = {
        permRootCause: problem.root_cause_statement,
        rootCauseCategory: problem.root_cause_category,
        permFix: (kb && kb.resolution) || problem.workaround || '',
        problemId: problem.problem_id
      };
      return finish(r);
    }

    // --- Verdict 3: known error -------------------------------------------
    if (top && top.confidence >= 0.55 && top.article.verification && top.article.verification.verified) {
      const a = top.article;
      r.verdict = 'known_error';
      r.confidence = Math.min(0.94, 0.6 + top.confidence * 0.35);
      r.headline = 'Known issue — verified fix exists in ' + a.kb_id + '. Skip discovery, go straight to the fix.';
      r.reasoning = [
        'Matched ' + a.kb_id + ' (' + Math.round(top.confidence * 100) + '%): ' + top.reasons.join(', ') + '.',
        'That article is verified and has been reused ' + (a.reuse_count || 0) + '×.',
        'Historical median for ' + ctx.service_component + ' is ' + (r.baseline_mins || '?') + ' min; applying a known fix should land well under that.',
        problem ? 'Linked Problem ' + problem.problem_id + ' (' + problem.status + ').' : 'No Problem record yet — consider promoting if this repeats.'
      ];
      const steps = stepsFromResolution(a.resolution);
      r.actions = [
        { seq: 1, action: 'Confirm the A/B distinction matches: ' + ((a.distinctions[0] && (a.distinctions[0].dimension + ' IS ' + a.distinctions[0].is + ' / IS NOT ' + a.distinctions[0].is_not)) || 'see article'), why: 'A KB hit is a hypothesis until the distinction matches', expected: 'Distinction confirmed', risk: 'None', est_mins: 10, source: a.kb_id }
      ].concat(steps.map((s, i) => ({
        seq: i + 2, action: s,
        why: 'Verified fix step from ' + a.kb_id,
        expected: i === steps.length - 1 ? 'Symptom cleared' : 'Step completes cleanly',
        risk: /purge|delete|remove|reset/i.test(s) ? 'Medium — destructive, confirm backup' : 'Low — reversible',
        est_mins: 10, source: a.kb_id
      }))).concat([{
        seq: steps.length + 2,
        action: 'Verify: ' + ((a.verification && a.verification.test) || 're-run the reproduction'),
        why: 'Closure requires the symptom to toggle off', expected: (a.verification && a.verification.result) || 'Symptom gone',
        risk: 'None', est_mins: 15, source: a.kb_id
      }]);
      r.fields = {
        permRootCause: a.root_cause,
        rootCauseCategory: (problem && problem.root_cause_category) || 'software-defect',
        permFix: a.resolution,
        permFixTest: (a.verification && a.verification.test) || '',
        workaround: a.workaround || ''
      };
      return finish(r);
    }

    // --- Verdict 4: new investigation -------------------------------------
    r.verdict = 'new_investigation';
    r.confidence = 0.55;
    r.headline = 'No confident match — run a minimal KT narrowing loop. ' +
      (hits.length ? 'Closest neighbours are weak matches, use them as hypotheses only.' : 'Genuinely novel; good KB candidate once solved.');
    r.reasoning = [
      hits.length ? 'Best KB match scored only ' + Math.round(hits[0].confidence * 100) + '% — below the action threshold.' : 'No KB article scored above threshold.',
      related.length ? 'This customer has ' + related.length + ' loosely related case(s); check whether they share a cause.' : 'No related customer history.',
      'Plan below is optimised for fewest single-variable tests, not for completeness.'
    ];
    r.actions = buildDiscoveryPlan(ctx, hits);
    return finish(r);
  }

  /** Minimal KT loop when nothing is known — 5 actions, not 20. */
  function buildDiscoveryPlan(ctx, hits) {
    const comp = ctx.service_component || 'the component';
    return [
      { seq: 1, action: 'Stop the bleed: identify and isolate the most recent change touching ' + comp, why: 'Config drift is the single most common root-cause category in this dataset', expected: 'Change identified or ruled out', risk: 'Low', est_mins: 15, source: 'history/pareto' },
      { seq: 2, action: 'Capture the A/B pair: one failing case and one working twin (same feature, different site/node/image)', why: 'Without a control there is no distinction, and without a distinction there is no root cause', expected: 'A and B recorded in §10.0.3', risk: 'None', est_mins: 20, source: 'KT method' },
      { seq: 3, action: 'List the differences between A and B and pick the single cleanest one', why: 'The best distinction is the one that most cleanly separates IS from IS NOT', expected: 'One variable selected', risk: 'None', est_mins: 10, source: 'KT method' },
      { seq: 4, action: 'Run ONE single-variable test on that distinction (reversible, one target only)', why: 'Multi-variable changes destroy the evidence you need', expected: 'Symptom toggles or does not', risk: 'Low — single target, reversible', est_mins: 25, source: 'KT method' },
      { seq: 5, action: hits.length ? 'If the test refutes, check the next neighbour: ' + hits[0].article.kb_id : 'If the test refutes, widen telemetry and repeat with the next distinction', why: 'Refuted fast is still progress', expected: 'Next hypothesis selected', risk: 'None', est_mins: 20, source: hits.length ? hits[0].article.kb_id : 'KT method' }
    ];
  }

  /* =====================================================================
   * TASK: probable_causes
   * Three ranked candidate causes, each with evidence for/against and ONE
   * single-variable test. Replaces the old hand-filled 8-column matrix.
   * ================================================================== */
  function doProbableCauses(ctx) {
    const t = doTriage(ctx);
    const r = base('probable_causes');
    r.verdict = t.verdict;
    r.confidence = t.confidence;
    r.evidence = t.evidence;
    r.baseline_mins = t.baseline_mins;
    r.baseline_label = t.baseline_label;
    r.reasoning = t.reasoning;

    const comp = ctx.service_component || 'the component';
    const site = ctx.site || 'this site';
    const kbEv = t.evidence.find(e => e.type === 'kb');
    const kb = kbEv ? kbById(kbEv.id) : null;
    const probEv = t.evidence.find(e => e.type === 'problem');
    const problem = probEv && global.Problems ? global.Problems.byId(probEv.id) : null;
    const priorCases = (global.Cases && ctx.customer_id)
      ? global.Cases.related({ customer_id: ctx.customer_id, service_component: ctx.service_component,
          error_signature_raw: ctx.error_signature_raw }, 4) : [];
    const caseIds = priorCases.map(x => x.case.ticket_id);

    const causes = [];

    if (problem || kb) {
      const src = problem || {};
      const statement = src.root_cause_statement || (kb && kb.root_cause) || 'Known cause from the knowledge base';
      causes.push({
        rank: 1,
        probability: problem ? 0.78 : 0.62,
        cause: statement,
        category: src.root_cause_category || 'software-defect',
        evidence_for: [
          problem ? 'Normalized error signature matches ' + problem.problem_id + ' (' + problem.status + ', seen ' + problem.recurrence_count + '×)'
                  : 'Closest KB match is ' + kb.kb_id + ', a verified fix reused ' + (kb.reuse_count || 0) + '×',
          caseIds.length ? 'This customer has prior cases with the same pattern: ' + caseIds.slice(0, 3).join(', ') : 'Signature matches the knowledge base entry',
          kb && kb.distinctions && kb.distinctions[0]
            ? 'Documented distinction: ' + kb.distinctions[0].dimension + ' IS ' + kb.distinctions[0].is + ' / IS NOT ' + kb.distinctions[0].is_not
            : 'Component and environment facets align with the matched record'
        ].filter(Boolean),
        evidence_against: [
          'Only holds if the A/B distinction in this incident matches the documented one — confirm before acting',
          problem && problem.status === 'resolved'
            ? problem.problem_id + ' was marked resolved, so a recurrence means the permanent fix regressed or was never fully deployed'
            : 'A KB hit is a hypothesis, not a verdict — an unrelated fault can produce a similar signature'
        ],
        test: (problem && problem.workaround)
          ? 'Apply ONLY the documented workaround on a single target and observe whether the symptom toggles: ' + problem.workaround
          : 'Apply only the first corrective step from ' + (kb ? kb.kb_id : 'the matched article') + ' to one target, changing nothing else',
        expected: 'Symptom clears on the treated target and remains present on untreated ones — a clean on/off toggle.',
        risk: 'Low — single target, reversible',
        est_mins: 20
      });
    }

    const generic = [
      { cause: 'A recent change to ' + comp + ' in ' + site + ' altered behaviour (config drift)',
        category: 'config-drift',
        for: ['Configuration drift is the most frequent coded root cause in this dataset',
              ctx.deviation ? 'Deviation began without a corresponding workload change' : 'No workload change reported'],
        against: ['Would be refuted if the change log for the window is genuinely empty',
                  'Does not explain the fault if unchanged peers fail identically'],
        test: 'Diff the running configuration of ' + comp + ' against the last known-good snapshot and revert ONE differing setting on a single node.',
        expected: 'Reverting the single setting toggles the symptom off on that node only.',
        risk: 'Low — single node, reversible', mins: 25 },
      { cause: 'Capacity or resource pressure on ' + comp + ' is degrading it under load',
        category: 'capacity',
        for: ['Symptom severity reported as varying rather than constant',
              'Capacity is the second most common coded cause in this dataset'],
        against: ['Would be refuted if utilisation metrics show no saturation during the impact window',
                  'Does not explain failures that occur at idle'],
        test: 'Re-run the reproduction at low load, or move one workload to an unloaded peer, holding configuration constant.',
        expected: 'Symptom disappears or measurably improves when load is removed.',
        risk: 'Low — one workload moved', mins: 25 },
      { cause: 'An upstream dependency of ' + comp + ' (network path, messaging, identity or storage) is the true source',
        category: 'third-party',
        for: ['The reported symptom surfaces at ' + comp + ' but may originate upstream',
              'Cross-component incidents in this dataset frequently present at the consumer, not the source'],
        against: ['Would be refuted if peer consumers of the same dependency are healthy',
                  'Does not explain the fault if the dependency\'s own health checks are all green'],
        test: 'Test the dependency directly from the affected node, bypassing ' + comp + ' entirely.',
        expected: 'The dependency test fails independently, proving the fault is upstream.',
        risk: 'None — read-only probe', mins: 20 }
    ];

    generic.forEach(g => {
      if (causes.length >= 3) return;
      causes.push({
        rank: causes.length + 1,
        probability: causes.length === 0 ? 0.45 : causes.length === 1 ? 0.22 : 0.12,
        cause: g.cause, category: g.category,
        evidence_for: g.for, evidence_against: g.against,
        test: g.test, expected: g.expected, risk: g.risk, est_mins: g.mins
      });
    });

    if (t.verdict === 'works_as_designed' && causes.length) {
      causes[0].probability = 0.9;
      causes[0].category = 'works-as-designed';
      causes[0].test = 'Reproduce once using the correct method and timing to confirm the behaviour sits inside the documented envelope.';
      causes[0].expected = 'Behaviour matches the documented envelope — no defect present, close as works-as-designed.';
    }

    r.causes = causes;
    r.actions = causes.map(c => ({
      seq: c.rank, action: c.test, why: c.cause,
      expected: c.expected, risk: c.risk, est_mins: c.est_mins,
      source: problem ? problem.problem_id : (kb ? kb.kb_id : 'KT method')
    }));
    r.headline = causes.length + ' probable causes ranked by prior probability. Test #1 first — it is the cheapest way to eliminate the most likely cause.';
    return finish(r);
  }

  /* =====================================================================
   * TASK: critique_plan — review what the engineer already wrote
   * ================================================================== */
  function doCritique(ctx) {
    const r = base('critique_plan');
    const rows = ctx.hypotheses || [];
    const t = doTriage(ctx);
    r.evidence = t.evidence;
    r.baseline_mins = t.baseline_mins;

    if (!rows.length) {
      r.verdict = 'empty_plan';
      r.headline = 'No hypotheses to review — generate a plan first.';
      r.confidence = 1;
      return finish(r);
    }

    const untested = rows.filter(h => h.outcome === 'untested');
    const refuted = rows.filter(h => h.outcome === 'refutes');
    const templates = rows.filter(h => /\[.*\]/.test(h.hypothesis));
    const noTest = rows.filter(h => !h.single_variable_test || h.single_variable_test.length < 15);
    const multiVar = rows.filter(h => /\band\b.*\band\b|both|simultaneously/i.test(h.single_variable_test || ''));

    if (templates.length) r.changes.push({ op: 'edit', target: 'rows ' + templates.map(h => rows.indexOf(h) + 1).join(', '), detail: 'Still contains template placeholders like [Site/Node A]', why: 'Placeholder hypotheses cannot be tested and inflate the plan' });
    if (t.verdict === 'known_error' && t.evidence[0]) r.changes.push({ op: 'add', target: 'new row #1', detail: 'Add the ' + t.evidence[0].id + ' root cause as the top hypothesis and test it first', why: 'A verified prior fix outranks any freshly invented hypothesis' });
    if (t.verdict === 'works_as_designed') r.changes.push({ op: 'remove', target: 'all rows', detail: 'Stop troubleshooting — signature matches a works-as-designed Problem', why: 'The plan is spending effort on behaviour that is not a fault' });
    refuted.forEach(h => r.changes.push({ op: 'remove', target: 'row ' + (rows.indexOf(h) + 1), detail: 'Marked refuted — archive it', why: 'Refuted hypotheses still consume attention in the matrix' }));
    noTest.forEach(h => r.changes.push({ op: 'edit', target: 'row ' + (rows.indexOf(h) + 1), detail: 'Single-variable test is missing or too vague to execute', why: 'An untestable hypothesis is an opinion' }));
    multiVar.forEach(h => r.changes.push({ op: 'edit', target: 'row ' + (rows.indexOf(h) + 1), detail: 'Test appears to change more than one variable', why: 'Multi-variable tests cannot attribute the result' }));
    if (untested.length > 3) r.changes.push({ op: 'reorder', target: 'matrix', detail: 'Rank the ' + untested.length + ' untested rows by (prior probability ÷ test cost) and run only the top 2', why: 'Minimum-action objective: most plans test too many things in parallel' });

    r.verdict = r.changes.length ? 'changes_suggested' : 'plan_ok';
    r.confidence = 0.8;
    r.headline = r.changes.length
      ? r.changes.length + ' change(s) suggested — plan can be shortened.'
      : 'Plan looks sound: hypotheses are concrete, tests are single-variable.';
    r.reasoning = [
      'Reviewed ' + rows.length + ' hypothesis row(s) against the KB, the customer history, and the KT single-variable rule.',
      'Objective is fewest actions to a confident answer, not exhaustive coverage.'
    ].concat(t.reasoning.slice(0, 2));
    return finish(r);
  }

  /* =====================================================================
   * TASK: root_cause
   * ================================================================== */
  function doRootCause(ctx) {
    const r = base('root_cause');
    const t = doTriage(ctx);
    r.evidence = t.evidence;

    const supporting = (ctx.hypotheses || []).filter(h => h.outcome === 'supports');
    const kb = t.evidence.find(e => e.type === 'kb');
    const prob = t.evidence.find(e => e.type === 'problem');
    const problem = prob && global.Problems ? global.Problems.byId(prob.id) : null;

    if (problem) {
      r.verdict = 'matched_problem';
      r.confidence = 0.87;
      r.headline = 'Root cause matches ' + problem.problem_id + ' — reuse its causal chain.';
      r.fields = {
        permRootCause: problem.root_cause_statement,
        rootCauseCategory: problem.root_cause_category,
        causalChain: problem.causal_chain
      };
      r.reasoning = ['Signature and component align with ' + problem.problem_id + '.',
        'Its causal chain has ' + problem.causal_chain.length + ' verified links.',
        'Confirm the last link still holds in this environment before accepting.'];
    } else if (kb) {
      const a = kbById(kb.id);
      r.verdict = 'derived_from_kb';
      r.confidence = 0.72;
      r.headline = 'Proposed root cause derived from ' + kb.id + ' — needs confirmation.';
      r.fields = {
        permRootCause: a.root_cause,
        rootCauseCategory: guessCategory(a),
        causalChain: (a.distinctions || []).map(d => ({ why: d.dimension + ' differs: ' + d.is + ' vs ' + d.is_not, evidence: 'A/B distinction' }))
      };
      r.reasoning = ['Nearest verified article is ' + kb.id + '.',
        supporting.length ? supporting.length + ' of your hypotheses are marked "supports" and align with it.' : 'No hypotheses marked "supports" yet — confirm before accepting.',
        'Root cause is only confirmed when the symptom toggles on and off with the suspected variable.'];
    } else {
      r.verdict = 'insufficient_evidence';
      r.confidence = 0.3;
      r.headline = 'Not enough evidence to propose a root cause.';
      r.reasoning = ['No KB article or Problem matched the signature.',
        'No hypothesis is marked "supports".',
        'Run at least one single-variable test that toggles the symptom, then re-run this.'];
    }
    return finish(r);
  }

  function guessCategory(a) {
    const t = ((a.root_cause || '') + ' ' + (a.tags || []).join(' ')).toLowerCase();
    if (/nearfull|capacity|saturat|exhaust|backfill/.test(t)) return 'capacity';
    if (/transceiver|optic|hardware|crc|disk fail/.test(t)) return 'hardware-fault';
    if (/runbook|procedure|did not include|omitted|manual/.test(t)) return 'human-error';
    if (/upstream|defect|bug|does not re-provision/.test(t)) return 'software-defect';
    if (/config|drift|setting|disabled|mismatch/.test(t)) return 'config-drift';
    if (/by design|expected|documented/.test(t)) return 'works-as-designed';
    return 'unknown';
  }

  /* =====================================================================
   * TASK: kb_draft
   * ================================================================== */
  function doKbDraft(ctx) {
    const r = base('kb_draft');
    const missing = [];
    if (!ctx.root_cause) missing.push('root cause');
    if (!ctx.permanent_fix) missing.push('permanent fix');
    if (!ctx.verification_result) missing.push('verification result');

    r.verdict = missing.length ? 'draft_with_gaps' : 'draft_ready';
    r.confidence = missing.length ? 0.55 : 0.85;
    r.headline = missing.length
      ? 'Draft prepared, but ' + missing.join(' + ') + ' still missing — retrieval quality will suffer.'
      : 'KB draft ready for review.';

    const dev = ctx.deviation || 'Untitled issue';
    r.fields = {
      title: (dev.length > 85 ? dev.slice(0, 82) + '…' : dev),
      symptom_tags: ctx.symptom_tags || [],
      issue_description: dev,
      searchable_summary: [ctx.service_component, ctx.environment, ctx.site, dev].filter(Boolean).join(' · '),
      suggested_tags: Array.from(new Set([ctx.service_component, ctx.category, ctx.site, ctx.environment,
        ctx.root_cause_category].filter(Boolean)))
    };
    r.reasoning = [
      'Title trimmed from the deviation statement — it is the highest-weight field in retrieval (×3).',
      'Tags derived from the controlled facets so the article stays filterable.',
      missing.length ? 'Gaps flagged above: an article without a verified fix ranks below verified ones and may never be recommended.'
        : 'All retrieval-critical fields present.'
    ];
    return finish(r);
  }

  /* =====================================================================
   * TASK: handover
   * ================================================================== */
  function doHandover(ctx) {
    const r = base('handover');
    const t = doTriage(ctx);
    const tested = (ctx.hypotheses || []).filter(h => h.actual_result);
    r.verdict = 'draft_ready';
    r.confidence = 0.8;
    r.headline = 'Handover drafted from the ticket, tests run, and matched history.';
    r.evidence = t.evidence;
    r.fields = {
      escOneLiner: [ctx.severity, ctx.deviation].filter(Boolean).join(' — ').slice(0, 160),
      escSteps: tested.length
        ? tested.map((h, i) => (i + 1) + '. ' + h.single_variable_test + ' → ' + h.actual_result).join('\n')
        : '(no single-variable tests recorded yet — L2 will ask for this first)',
      escLeads: t.evidence.length
        ? t.evidence.slice(0, 3).map(e => e.id + ': ' + e.label).join('\n')
        : 'No strong KB or history match.',
      escReasonDetail: ({
        known_error: 'Known fix identified but requires privileges/change window beyond this tier.',
        recurrence: 'Recurrence of a tracked Problem — needs Problem owner, not incident handling.',
        works_as_designed: 'Confirming expected behaviour with the platform owner before closing.',
        new_investigation: 'No KB match; needs deeper platform expertise to isolate the distinction.'
      })[t.verdict]
    };
    r.reasoning = ['Summary built from severity + deviation.',
      'Steps section pulled from hypothesis rows that have an actual result — the part L2 always asks for.',
      'Leads section cites the matched KB/Problem/case ids so the next tier does not redo the search.'];
    return finish(r);
  }

  /* =====================================================================
   * TASK: infra_health — fleet-level diagnostic for the dashboard
   * ================================================================== */
  function doInfraHealth(ctx) {
    const r = base('infra_health');
    const range = (ctx && ctx.range) || '7d';
    if (!global.Analytics) {
      r.verdict = 'no_data';
      r.headline = 'Analytics layer unavailable.';
      return finish(r);
    }

    const h = global.Analytics.health(range);
    const c = h.counts;
    const comps = global.Analytics.topBy(range, 'service_component', 3);
    const custs = global.Analytics.topBy(range, 'customer_id', 3);
    const causes = global.Analytics.topBy(range, 'root_cause_category', 3);

    r.verdict = h.band === 'healthy' ? 'infra_healthy' : h.band === 'degraded' ? 'infra_degraded' : 'infra_at_risk';
    r.confidence = 0.8;
    r.health = h;
    r.headline = 'Infrastructure health ' + h.score + '/100 (' + h.band + ') — ' +
      c.total + ' cases, ' + c.pending + ' pending, ' + c.solved + ' solved over the ' +
      (global.Analytics.RANGES[range] || {}).label.toLowerCase() + '.';

    comps.forEach(x => r.evidence.push({
      type: 'case', id: x.key,
      label: x.count + ' cases (' + x.open + ' open, ' + x.p1 + ' P1)',
      why: 'top component by volume'
    }));
    (global.PROBLEM_STORE || []).filter(p => ['known-error', 'investigating'].includes(p.status))
      .slice(0, 3).forEach(p => r.evidence.push({
        type: 'problem', id: p.problem_id, label: p.title,
        why: p.status + ', seen ' + p.recurrence_count + '×'
      }));

    r.reasoning = h.factors.length
      ? h.factors.map(f => '−' + f.pts + ' pts: ' + f.why)
      : ['No score deductions — all monitored indicators are inside target.'];
    r.reasoning.push('Top components: ' + comps.map(x => x.key + ' (' + x.count + ')').join(', ') + '.');
    r.reasoning.push('Most affected customers: ' + custs.map(x => x.key + ' (' + x.count + ')').join(', ') + '.');

    let seq = 0;
    const act = (action, why, expected, mins, risk) =>
      r.actions.push({ seq: ++seq, action, why, expected, risk: risk || 'None', est_mins: mins, source: 'fleet analytics' });

    if (c.p1_open > 0) act('Review the ' + c.p1_open + ' open P1 incident(s) before anything else',
      'Open P1s dominate both customer perception and the health score', 'P1 count trending to zero', 30);
    if (comps[0] && comps[0].count > 3) act('Run a component review on ' + comps[0].key + ' — ' + comps[0].count + ' cases this period',
      'A single component concentrating failures usually indicates one unfixed underlying Problem, not many separate faults',
      'Cases consolidate into one or two Problem records', 60);
    if (causes[0] && causes[0].key !== 'works-as-designed') act('Target the top root-cause category: ' + causes[0].key + ' (' + causes[0].count + ' cases)',
      'Fixing the most frequent cause class removes the most repeat work', 'Category share falls next period', 90);
    if (c.wad > 2) act('Close the documentation gap behind ' + c.wad + ' works-as-designed tickets',
      'These consumed support time for behaviour that is not a fault', 'Fewer WAD tickets raised', 45);
    if (c.open_problems > 0) act('Drive the ' + c.open_problems + ' open Problem(s) to a permanent fix',
      'Every open Problem is a guaranteed source of future incidents', 'Recurrence count stops rising', 120);
    if (!seq) act('No corrective action required — hold current cadence',
      'All indicators inside target for this period', 'Health stays above 80', 0);

    return finish(r);
  }

  /* =====================================================================
   * TASK: cluster
   * ================================================================== */
  function doCluster() {
    const r = base('cluster');
    const cands = global.Problems ? global.Problems.clusterCandidates(2) : [];
    r.verdict = cands.length ? 'clusters_found' : 'no_clusters';
    r.confidence = 0.75;
    r.headline = cands.length
      ? cands.length + ' candidate Problem(s) found in unlinked cases.'
      : 'No unlinked cases share a signature — nothing to cluster.';
    cands.forEach((c, i) => {
      r.evidence.push({
        type: 'case', id: c.cases.map(x => x.ticket_id).join(', '),
        label: c.cases[0].title,
        why: c.cases.length + ' cases · ' + c.customers.length + ' customer(s) · ' + c.total_mins + ' min spent'
      });
      r.actions.push({
        seq: i + 1,
        action: 'Create a Problem from ' + c.cases.length + ' cases sharing "' + c.signature.slice(0, 60) + '…"',
        why: c.customers.length > 1 ? 'Affects ' + c.customers.length + ' customers — systemic' : 'Repeating for one customer',
        expected: 'Problem record created, cases linked',
        risk: 'None', est_mins: 15, source: 'clustering'
      });
    });
    r.reasoning = ['Grouped closed cases with no problem_id by identical normalized error signature.',
      'Clusters spanning multiple customers are systemic and should be prioritised.',
      'Each cluster represents repeated effort that a single permanent fix would eliminate.'];
    return finish(r);
  }

  /* =====================================================================
   * Common finish: cost the plan
   * ================================================================== */
  function finish(r) {
    if (r.actions.length) {
      r.est_total_mins = r.actions.reduce((s, a) => s + (a.est_mins || 0), 0);
      if (r.baseline_mins) r.est_saving_mins = Math.max(0, r.baseline_mins - r.est_total_mins);
    }
    return r;
  }

  /* =====================================================================
   * Inference boundary — THE ONLY THING A REAL MODEL REPLACES
   * ================================================================== */
  const HANDLERS = {
    triage: doTriage,
    probable_causes: doProbableCauses,
    critique_plan: doCritique,
    root_cause: doRootCause,
    kb_draft: doKbDraft,
    handover: doHandover,
    cluster: doCluster,
    infra_health: doInfraHealth
  };

  async function _infer(task, ctx) {
    if (CONFIG.mode === 'api' && CONFIG.endpoint) {
      const res = await fetch(CONFIG.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task, context: ctx, schema: 'AIResult/1.0' })
      });
      if (!res.ok) throw new Error('agent API ' + res.status);
      return res.json();
    }
    const fn = HANDLERS[task];
    if (!fn) throw new Error('unknown task: ' + task);
    return fn(ctx);
  }

  const AIAgent = {
    CONFIG,
    TASKS,
    configure(o) { Object.assign(CONFIG, o || {}); return CONFIG; },
    async run(task, ctx) {
      await sleep(rnd());                       // simulate inference latency
      return _infer(task, ctx || {});
    },
    _infer
  };

  global.AIAgent = AIAgent;
})(window);
