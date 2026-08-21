/* =============================================================================
 * kt_pipeline.js — the Universal Troubleshooting Pipeline (8 stages)
 *
 *   IMPACT → PRIORITIZE → CONTAIN → DEFINE → NARROW → TEST → CONFIRM → FIX & LEARN
 *
 *   "Stop the impact. Narrow the difference. Test one variable.
 *    Prove the cause. Fix it forever."
 *
 * Replaces the 10-step Kepner-Tregoe sequence as the operator-facing model.
 * The KT method is unchanged underneath — the ten steps collapse onto these
 * eight with no orphans (see LEGACY_MAP). What changes is that an
 * operator now holds eight things in their head instead of ten, and that the
 * middle three are explicitly a LOOP rather than three more items in a list.
 *
 * Three design rules this file enforces, because prose could not:
 *
 *   1. A stage is DONE when its fields are filled — never when someone ticks
 *      a box. `completes` lists the field ids, and progress() computes status.
 *      A progress bar you can advance by clicking is a progress bar that lies.
 *
 *   2. NARROW → TEST → CONFIRM is a cycle, not three steps. LOOP names them,
 *      and progress inside the loop is measured in hypotheses eliminated, not
 *      stages passed, so an operator on iteration 3 can see they moved.
 *
 *   3. The next action is chosen by expected time-to-resolution, not by which
 *      cause scores highest. See nextBestAction().
 *
 * Pure data + pure functions: no DOM, no globals beyond the export, so it runs
 * under node for tests. The UI passes in a `read(fieldId)` accessor.
 *
 * Exposes: Pipeline
 * ========================================================================== */
(function (global) {
  'use strict';

  const GOVERNING_RULE =
    'Stop the impact. Narrow the difference. Test one variable. ' +
    'Prove the cause. Fix it forever.';

  /* ---------------------------------------------------------------------
   * The eight stages.
   *
   *   key       stable id, used by demo data and saved tickets
   *   n         display order
   *   question  the ONE thing this stage asks
   *   output    what the stage must produce before it counts as done
   *   completes field ids that, when non-empty, satisfy the stage
   *   needs     how many of `completes` must be filled (default: all)
   *   legacy    the KT step numbers this stage absorbs
   * ------------------------------------------------------------------- */
  const STAGES = [
    {
      key: 'impact', n: 1, icon: '🔴', label: 'IMPACT',
      title: 'Size the impact and capture evidence',
      where: '§0 · §1.1.3 · §1.2.7 · §5',
      question: 'What is affected and how serious is it?',
      output: 'Priority + blast radius + evidence captured',
      why: 'Do not troubleshoot yet. Size the damage and snapshot the failing ' +
           'state — a fix applied too early destroys what you need to investigate.',
      /* Evidence capture lives HERE, not as a footnote in CONTAIN. It is the
         most common way an incident becomes permanently unsolvable, so it is
         an output of the first stage and a gate on everything after it. */
      completes: ['severity', 'blastRadius', 'p1_3'],
      evidence_fields: ['errMsg', 'logs', 'repro'],
      est_mins: 20, legacy: [1]
    },
    {
      /* Split back out of IMPACT: sizing the damage and deciding what to save
         are different acts, and the second is the one that gets skipped under
         pressure. It has its own output and its own gate. */
      key: 'prioritize', n: 2, icon: '🚦', label: 'PRIORITIZE',
      title: 'Decide what to protect first',
      where: '§10.2 Prioritize',
      question: 'What must we protect or restore first?',
      output: 'The action to do first, and why it beat the others',
      why: 'Deal with the greatest current or potential impact first. Rank the ' +
           'actions available by what each one PROTECTS per minute spent, and ' +
           'never let an irreversible action be the quick win.',
      completes: ['__prioritize'],   // computed: see progress()
      gated_on: 'impact',            // decide nothing before the damage is sized
      skippable: true,
      est_mins: 15, legacy: [2]
    },
    {
      key: 'contain', n: 3, icon: '🛡️', label: 'CONTAIN',
      title: 'Reduce the impact safely and reversibly',
      where: '§10.3 Temporary Fix',
      question: 'How do we reduce the impact safely right now?',
      output: 'Mitigation / workaround — and the impact after it',
      why: 'Find the smallest safe REVERSIBLE action that reduces impact ' +
           'without hiding the cause. Failover · rollback · disable · bypass · ' +
           'route around · scale · isolate · rate-limit. MITIGATION IS NOT ROOT CAUSE.',
      completes: ['tmpFixSteps'], needs: 1,
      also: ['tmpFixTest', 'tmpFixResult', 'tmpFixRollback'],
      /* Gated on IMPACT, not on PRIORITIZE. The evidence gate has a real
         irreversibility argument behind it — mitigate first and the state you
         needed is gone. "You have not ranked your options yet" does not: it
         would block containment on a ranking table, which is the bureaucratic
         friction this pipeline is supposed to remove. PRIORITIZE still reports
         incomplete until an action is committed; it just does not stop work. */
      gated_on: 'impact',
      skippable: true,              // see skipReason()
      est_mins: 25, legacy: [3]
    },
    {
      key: 'define', n: 4, icon: '🎯', label: 'DEFINE',
      title: 'State the deviation and bound it',
      where: '§1.1 · §1.2 · §1.4',
      question: 'What exactly is failing, and what comparable thing is working?',
      output: 'Deviation + IS / IS NOT',
      why: 'One sentence naming an object and a defect, with no theory in it — ' +
           'then bound it. The unaffected comparison population is the more ' +
           'informative half.',
      completes: ['p1_1', 'p1_2', 'extent_is', 'extent_isnot'],
      est_mins: 30, legacy: [4, 5]
    },
    {
      key: 'narrow', n: 5, icon: '🔎', label: 'NARROW',
      title: 'Find what is different, and what changed',
      where: '§1.1.4 · §6 · §10.4 A/B',
      question: 'What is different, and what changed?',
      output: 'Distinctions + changes',
      why: 'Divide the search space: all customers? all sites? all nodes? all ' +
           'versions? Keep splitting until few mechanisms remain — then ask ' +
           'what changed around that difference.',
      completes: ['extent_dist', 'changes'], needs: 1,
      est_mins: 25, legacy: [6]
    },
    {
      key: 'test', n: 6, icon: '🧪', label: 'TEST',
      title: 'Rank the candidates, eliminate one',
      where: '§10.5 Probable Causes',
      question: 'Which possible cause can we eliminate next?',
      output: 'Ranked candidates + one single-variable test',
      /* Generating candidates and killing them are different skills, and the
         10-step model kept them apart for that reason. Merged here, the card
         keeps two halves — candidates first, then the next test — so a junior
         still gets the prompt to generate BEFORE testing. */
      why: 'Derive candidates from the distinctions and changes, not from ' +
           'memory. Then change ONE variable, reversibly, on ONE target. ' +
           'Optimise for the best discriminating test, not the likeliest cause.',
      completes: ['__hypotheses'],   // computed: see progress()
      est_mins: 45, legacy: [7, 8]
    },
    {
      key: 'confirm', n: 7, icon: '✅', label: 'CONFIRM',
      title: 'Prove the cause controls the symptom',
      where: '§10.7 Confirm gate',
      question: 'Can we prove the cause controls the symptom?',
      output: 'Confirmed root cause — or back to NARROW',
      /* A GATE, not a workspace. Its act is the same as TEST; the difference
         is that this is the test that survived. It renders as a three-way
         verdict, which is what stops a fix shipping on correlation. */
      why: 'The cause must explain the IS *and* the IS NOT, and changing it ' +
           'must make the symptom appear or disappear on demand.',
      gate: true,
      verdicts: [
        { key: 'confirmed', label: 'CONFIRMED', next: 'fix' },
        { key: 'refuted', label: 'REFUTED', next: 'narrow' },
        { key: 'inconclusive', label: 'INCONCLUSIVE', next: 'test' }
      ],
      completes: ['permRootCause', 'permFixResults'],
      est_mins: 30, legacy: [9]
    },
    {
      key: 'fix', n: 8, icon: '🔧', label: 'FIX & LEARN',
      title: 'Fix permanently, prevent, publish, watch',
      where: '§10.8 Permanent Fix & Close',
      question: 'How do we fix it permanently and stop recurrence?',
      output: 'Fix + validation + prevention + KB + watch window',
      why: 'Apply the minimal safe correction, confirm normal operation, ' +
           'identify why this was possible at all, add a guardrail, publish ' +
           'the article, and watch for recurrence.',
      completes: ['permFix', 'permFixResults', 'permPrevention'],
      est_mins: 45, legacy: [10]
    }
  ];

  /* The middle three are a cycle. Everything about how progress is displayed
     inside them differs from the linear stages, so they are named once here. */
  const LOOP = ['narrow', 'test', 'confirm'];

  /* Old 10-step number -> new stage key. Used to migrate saved tickets and the
     demo data without losing a single note. */
  const LEGACY_MAP = {
    1: 'impact', 2: 'prioritize',
    3: 'contain',
    4: 'define', 5: 'define',
    6: 'narrow',
    7: 'test', 8: 'test',
    9: 'confirm',
    10: 'fix'
  };

  /* One row per pass around NARROW → TEST → CONFIRM. This is the record that
     makes the funnel auditable and lets the handover write itself; a freeform
     notes textarea cannot do either. */
  const LOOP_ENTRY_FIELDS = ['n', 'distinction', 'variable', 'expected',
    'actual', 'verdict', 'next'];

  const STATUS = ['not-started', 'in-progress', 'done', 'n/a'];

  const byKey = k => STAGES.find(s => s.key === k) || null;
  const filled = v => String(v == null ? '' : v).trim() !== '';

  /* ---------------------------------------------------------------------
   * Should CONTAIN be skipped?
   *
   * Forcing containment on a ticket with no spreading impact is friction that
   * teaches operators to click past stages — which is how a progress bar
   * becomes decorative. Severity, blast radius and trend are already captured,
   * so the answer is derivable rather than asked for.
   * ------------------------------------------------------------------- */
  function skipReason(stage, ctx) {
    /* Both impact-response stages share the test: if there is nothing to
       contain there is nothing to prioritise protecting either. */
    if (stage.key !== 'contain' && stage.key !== 'prioritize') return null;
    const sev = String(ctx.severity || '');
    const blast = String(ctx.blast_radius || '');
    if (ctx.impact_growing) return null;
    if (ctx.verdict === 'works_as_designed') {
      return 'not a fault — nothing to contain';
    }
    /* Only the genuinely narrow radii skip containment. "Users at one site" is
       still a lot of people, so it does not qualify. */
    if ((sev === '3' || sev === '4') &&
        (blast === 'specific-user' || blast === 'tenant')) {
      return 'S' + sev + ' affecting ' +
        (blast === 'tenant' ? 'one tenant' : 'one user or workload') + ', impact not growing';
    }
    return null;
  }

  /* ---------------------------------------------------------------------
   * progress(read, ctx)
   *
   * `read(fieldId)` returns the current value of a form field.
   * `ctx` carries what cannot be read from a single field: the hypothesis
   * list, the triage verdict, the loop log.
   *
   * Status is COMPUTED. There is deliberately no way to mark a stage done by
   * hand — a bar you can advance by clicking tells you nothing.
   * ------------------------------------------------------------------- */
  function progress(read, ctx) {
    const c = ctx || {};
    const hyps = c.hypotheses || [];
    const r = f => (typeof read === 'function' ? read(f) : '');

    const stages = STAGES.map(s => {
      const skip = skipReason(s, c);
      if (skip) {
        return { stage: s, status: 'n/a', reason: skip, done: 0, total: 0, missing: [] };
      }

      let done, total, missing = [];

      if (s.key === 'test') {
        // TEST is satisfied by candidates that have actually been exercised,
        // not by a filled textbox.
        total = 2;
        const tested = hyps.filter(h => h.outcome && h.outcome !== 'untested').length;
        done = (hyps.length ? 1 : 0) + (tested ? 1 : 0);
        if (!hyps.length) missing.push('no candidate causes yet');
        else if (!tested) missing.push('no candidate tested yet');
      } else if (s.key === 'prioritize') {
        /* Listing options is not deciding. The stage is done only when one
           action is actually committed to — otherwise the bar would go green
           on the stage whose entire purpose is to make a choice. */
        const p = c.prioritize || { listed: 0, chosen: false };
        total = 2;
        done = (p.listed ? 1 : 0) + (p.chosen ? 1 : 0);
        if (!p.listed) missing.push('no candidate actions listed');
        else if (!p.chosen) missing.push('no action chosen to do first');
      } else {
        const fields = s.completes || [];
        total = s.needs || fields.length;
        const have = fields.filter(f => filled(r(f)));
        done = Math.min(have.length, total);
        missing = fields.filter(f => !filled(r(f)));
      }

      // Evidence capture is a first-class output of PRIORITIZE, so a stage 1 with
      // its numbers but no captured state is IN PROGRESS, never done.
      let evidence_ok = true;
      if (s.evidence_fields) {
        evidence_ok = s.evidence_fields.some(f => filled(r(f)));
        if (!evidence_ok) missing.push('no evidence captured (error text, logs or repro)');
      }

      const complete = total > 0 && done >= total && evidence_ok;
      let status = complete ? 'done' : (done > 0 ? 'in-progress' : 'not-started');

      /* The status recorded on the plan can DOWNGRADE this, never upgrade it.
         Filling every field but knowing you are not finished is a real state;
         declaring an empty stage "done" is not. Without this the bar and the
         plan contradict each other on any part-finished ticket. */
      const card = (c.card_status || {})[s.key];
      if (card === 'n/a' || card === 'skipped') {
        return { stage: s, status: 'n/a', reason: 'marked not required', done: 0, total: 0, missing: [] };
      }
      /* Only 'in-progress' downgrades. 'not-started' is the plan's DEFAULT, so
         treating it as a veto would mean a freshly loaded plan could hold a
         fully-filled stage at "not done" forever. */
      if (status === 'done' && card === 'in-progress') {
        status = 'in-progress';
        missing = missing.concat(['the plan still marks this stage in progress']);
      }

      return {
        stage: s, status, done, total, missing,
        blocked: null   // resolved in the second pass
      };
    });

    // Second pass: a stage gated on another cannot start before it is done.
    const index = {};
    stages.forEach(x => { index[x.stage.key] = x; });
    stages.forEach(x => {
      const g = x.stage.gated_on;
      if (g && index[g] && index[g].status !== 'done' && x.status === 'not-started') {
        x.blocked = g;
      }
    });

    const live = stages.filter(x => x.status !== 'n/a');
    const current = live.find(x => x.status === 'in-progress') ||
                    live.find(x => x.status === 'not-started') ||
                    live[live.length - 1];

    /* A candidate can be killed from either side — a cause card marked
       "refutes", or a logged loop pass with a "refutes" verdict. They usually
       describe the same kill, so take the larger rather than the sum. */
    const loop = c.loop_log || [];
    const eliminated = Math.max(
      hyps.filter(h => h.outcome === 'refutes').length,
      loop.filter(x => x.verdict === 'refutes').length
    );

    return {
      stages,
      current: current ? current.stage.key : 'impact',
      in_loop: current ? LOOP.indexOf(current.stage.key) >= 0 : false,
      /* Inside the loop, "3 of 7 stages" is a meaningless number — an operator
         on their third pass has advanced nothing on a linear bar. What has
         actually moved is how many candidates are dead. */
      iteration: Math.max(loop.length, eliminated ? 1 : 0),
      eliminated,
      surviving: hyps.filter(h => !h.outcome || h.outcome === 'untested').length,
      done_count: live.filter(x => x.status === 'done').length,
      live_count: live.length,
      remaining_mins: live
        .filter(x => x.status !== 'done')
        .reduce((t, x) => t + (x.stage.est_mins || 0), 0)
    };
  }

  /* ---------------------------------------------------------------------
   * nextBestAction(ctx)
   *
   * The one thing shown prominently under the progress bar.
   *
   * Shortcuts fire FIRST and unconditionally: the cheapest test is the one you
   * do not run because somebody already answered it. Only when nothing is
   * known does this fall through to scoring.
   *
   * Scoring objective is expected time-to-resolution, NOT confidence:
   *
   *     score = P(this test ends the investigation) / cost
   *
   * with irreversible tests excluded while any reversible one remains. Ranking
   * by confidence alone is what sends an engineer to the plausible-but-wrong
   * candidate first; ranking by "chance of finishing per minute", gated on
   * reversibility, is what they actually do when they are working well.
   * ------------------------------------------------------------------- */
  function nextBestAction(ctx) {
    const c = ctx || {};
    const prog = c.progress || null;

    const act = (kind, headline, why, target) =>
      ({ kind, headline, why, target: target || null });

    // --- shortcuts: do not troubleshoot at all -------------------------
    if (c.duplicate_of) {
      return act('stop',
        'Do not work this ticket — join ' + c.duplicate_of,
        'The same issue is already open. Two engineers on two tickets is the ' +
        'most expensive way to solve one problem.', c.duplicate_of);
    }
    if (c.verdict === 'works_as_designed') {
      return act('stop',
        'Do not troubleshoot — this is documented behaviour',
        'Send the customer the documented behaviour and the correct validation ' +
        'method, set root cause = works-as-designed, and close.', c.kb_id);
    }
    if (c.verdict === 'known_error' || c.verdict === 'recurrence') {
      return act('apply',
        'Do not re-derive — apply ' + (c.kb_id || c.problem_id) + ' to ONE target',
        'The cause is already proven and the distinction already documented. ' +
        'Confirm the same distinction holds here, then verify on a single ' +
        'reversible target.', c.kb_id || c.problem_id);
    }

    // --- otherwise: pick the test with the best chance of finishing ----
    const open = (c.hypotheses || []).filter(h => !h.outcome || h.outcome === 'untested');
    if (open.length) {
      const reversible = h => !/irreversible|high/i.test(String(h.risk || ''));
      const pool = open.some(reversible) ? open.filter(reversible) : open;

      const scored = pool.map(h => {
        const p = typeof h.probability === 'number' ? h.probability : 0.33;
        const cost = Math.max(5, h.est_mins || 25);
        /* A test that can only ever confirm one candidate teaches less than one
           whose outcome splits the field; the bonus is small so it breaks ties
           rather than overriding the primary objective. */
        const discriminates = 1 + 0.15 * (1 - Math.abs(2 * p - 1));
        return { h, score: (p * discriminates) / cost };
      }).sort((a, b) => b.score - a.score);

      const best = scored[0].h;
      return act('test',
        best.single_variable_test || best.test || ('Test: ' + best.hypothesis),
        'Highest chance of ending the investigation per minute spent' +
        (best.risk ? ' · ' + best.risk : '') +
        (best.est_mins ? ' · ~' + best.est_mins + ' min' : '') +
        ' · tests exactly one variable.',
        best.hypothesis);
    }

    // --- no candidates: advance the pipeline ---------------------------
    const cur = prog ? byKey(prog.current) : STAGES[0];
    const st = prog ? prog.stages.find(x => x.stage.key === prog.current) : null;
    if (st && st.blocked) {
      const g = byKey(st.blocked);
      return act('gate',
        'Finish ' + g.label + ' first — ' + g.output,
        g.label + ' gates ' + cur.label + '. ' + g.why, g.key);
    }
    return act('stage',
      cur.icon + ' ' + cur.label + ' — ' + cur.question,
      (st && st.missing.length ? 'Still missing: ' + st.missing.join(', ') + '. ' : '') +
      cur.why,
      cur.key);
  }

  /* ---------------------------------------------------------------------
   * migratePlan(tenEntries)
   *
   * Converts a legacy 10-step [status, notes] array into 7 stage entries.
   * Notes from merged steps are joined, never dropped. Status of a merged
   * pair is the LEAST advanced of the two, so a half-finished pair reads as
   * in-progress rather than silently claiming to be done.
   * ------------------------------------------------------------------- */
  const RANK = { 'not-started': 0, 'in-progress': 1, 'done': 2, 'skipped': 3, 'n/a': 3 };

  function migratePlan(ten) {
    if (!Array.isArray(ten)) return [];
    const out = {};
    ten.forEach((entry, i) => {
      const key = LEGACY_MAP[i + 1];
      if (!key) return;
      const [status, notes] = entry || [];
      if (!out[key]) out[key] = { status: null, notes: [] };
      const cur = out[key];
      if (cur.status == null) cur.status = status;
      else if (RANK[status] < RANK[cur.status]) cur.status = status;
      if (notes) cur.notes.push(notes);
    });
    return STAGES.map(s => {
      const e = out[s.key] || { status: 'not-started', notes: [] };
      return [e.status || 'not-started', e.notes.join(' · ')];
    });
  }

  global.Pipeline = {
    STAGES, LOOP, LEGACY_MAP, STATUS, GOVERNING_RULE, LOOP_ENTRY_FIELDS,
    byKey, progress, nextBestAction, migratePlan, skipReason
  };
})(typeof window !== 'undefined' ? window : globalThis);
