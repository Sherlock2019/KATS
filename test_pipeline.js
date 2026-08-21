#!/usr/bin/env node
/* =============================================================================
 * test_pipeline.js — checks the 8-stage pipeline against the real demo data.
 *
 *   node test_pipeline.js
 *
 * No dependencies. Scripts run in a vm context with the two browser globals
 * they touch (`window`, `localStorage`), in the SAME order the page loads them.
 *
 * What it guards:
 *   - the 8 stages still absorb all 10 legacy KT steps exactly once, so an
 *     archived ticket can always be migrated without losing a step;
 *   - stage status is computed from fields — in particular PRIORITIZE cannot be
 *     "done" without captured evidence, and CONTAIN stays gated behind it;
 *   - CONTAIN auto-skips only when the impact is genuinely bounded;
 *   - nextBestAction takes the shortcut before it scores anything, and never
 *     recommends an irreversible test while a reversible one exists.
 * ========================================================================== */
'use strict';

const fs = require('fs');
const vm = require('vm');
const path = require('path');

const DIR = __dirname;
const store = {};
const sandbox = {
  console, setTimeout, clearTimeout, Promise,
  localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; }
  },
  Date, Math, JSON, Object, Array, String, Number, Set, Map, RegExp,
  isNaN, parseInt, parseFloat
};
sandbox.window = sandbox;
vm.createContext(sandbox);

/* Same order as the <script> tags: kt_pipeline before ai_agent, because the
   agent reads the stage list rather than defining one. */
['kb_database.js', 'kt_data.js', 'kt_topology.js', 'kt_pipeline.js',
 'ai_agent.js', 'demo_tickets.js']
  .forEach(f => vm.runInContext(fs.readFileSync(path.join(DIR, f), 'utf8'), sandbox, { filename: f }));

const { Pipeline, DEMO_TICKETS, LEGACY_KT_STEPS, AIAgent } = sandbox;

let fails = 0;
const ok = (c, m) => { console.log((c ? '  ok   ' : '  FAIL ') + m); if (!c) fails++; };

console.log('\n== shape ==');
ok(Pipeline.STAGES.length === 8, '8 stages');
ok(LEGACY_KT_STEPS.length === 10, 'the legacy 10-step list is still available for migration');
const covered = Pipeline.STAGES.reduce((a, x) => a.concat(x.legacy), []).sort((a, b) => a - b);
ok(JSON.stringify(covered) === JSON.stringify([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]),
  'the 8 stages absorb all 10 KT steps exactly once: ' + covered.join(','));
ok(Pipeline.LOOP.join(' > ') === 'narrow > test > confirm', 'the loop is NARROW > TEST > CONFIRM');
ok(Pipeline.STAGES.every(s => s.key && s.icon && s.title && s.question && s.output && s.why),
  'every stage has an icon, title, question, output and rationale');

console.log('\n== demo data is already on the 8-stage model ==');
DEMO_TICKETS.filter(t => t.plan).forEach(t => {
  ok(t.plan.length === 8, t.id + ': plan has 8 stage entries');
  ok(t.plan.every(e => Pipeline.STATUS.indexOf(e[0]) >= 0 || e[0] === 'skipped'),
    t.id + ': every entry carries a valid status');
});
ok(DEMO_TICKETS.filter(t => t.plan).length === 10, '10 demo tickets carry a plan');

console.log('\n== migratePlan, on genuinely legacy input ==');
const legacy = [
  ['done', 'a'],                              // IMPACT
  ['not-started', 'b'],                       // PRIORITIZE
  ['done', 'c'],                              // CONTAIN
  ['done', 'd'], ['in-progress', 'e'],        // DEFINE
  ['done', 'f'],                              // NARROW
  ['done', 'g'], ['done', 'h'],               // TEST
  ['done', 'i'],                              // CONFIRM
  ['in-progress', 'j']                        // FIX
];
const mig = Pipeline.migratePlan(legacy);
ok(mig.length === 8, '10 legacy entries collapse to 8');
ok(mig[1][0] === 'not-started', 'step 2 lands on PRIORITIZE, not merged into IMPACT');
ok(mig[3][0] === 'in-progress', 'DEFINE still merges steps 4+5, taking in-progress');
ok(mig[0][1] === 'a' && mig[1][1] === 'b', 'IMPACT and PRIORITIZE keep their own notes');
ok(mig.map(e => e[1]).join('').split(' · ').join('') === 'abcdefghij',
  'every legacy note survives the merge, in order');

console.log('\n== progress is computed from fields, not clicked ==');
const blank = Pipeline.progress(() => '', {});
ok(blank.done_count === 0, 'a blank ticket has 0 stages done');
ok(blank.current === 'impact', 'a blank ticket sits at IMPACT');

const full = {
  severity: '2', blastRadius: 'specific-user', p1_3: 'Cannot deliver 12 VMs',
  errMsg: 'No port found in network None'
};
ok(Pipeline.progress(k => full[k] || '', {}).stages[0].status === 'done',
  'IMPACT completes on severity + blast radius + impact + evidence');

const noEvidence = Object.assign({}, full); delete noEvidence.errMsg;
const pe = Pipeline.progress(k => noEvidence[k] || '', {});
ok(pe.stages[0].status === 'in-progress',
  'IMPACT is NOT done without captured evidence — ' + pe.stages[0].missing.join('; '));
ok(pe.stages[1].blocked === 'impact', 'PRIORITIZE is blocked until IMPACT is done');

console.log('\n== a stage card can downgrade the computed status, never upgrade it ==');
ok(Pipeline.progress(k => full[k] || '', { card_status: { impact: 'in-progress' } })
    .stages[0].status === 'in-progress',
  'an explicit in-progress holds a fully-filled stage open');
ok(Pipeline.progress(k => full[k] || '', { card_status: { impact: 'not-started' } })
    .stages[0].status === 'done',
  'the card DEFAULT of not-started does not veto filled fields');
ok(Pipeline.progress(() => '', { card_status: { impact: 'done' } }).stages[0].status !== 'done',
  'ticking done on an empty stage does nothing');
ok(Pipeline.progress(k => full[k] || '', { card_status: { contain: 'n/a' } })
    .stages[2].status === 'n/a',
  'a stage marked not required stays not required');

console.log('\n== PRIORITIZE is a decision, not a list ==');
ok(Pipeline.progress(k => full[k] || '', { prioritize: { listed: 0, chosen: false } })
    .stages[1].status === 'not-started', 'no actions listed -> not started');
const listedOnly = Pipeline.progress(k => full[k] || '', { prioritize: { listed: 4, chosen: false } });
ok(listedOnly.stages[1].status === 'in-progress',
  'listing options is not deciding — ' + listedOnly.stages[1].missing.join('; '));
ok(Pipeline.progress(k => full[k] || '', { prioritize: { listed: 4, chosen: true } })
    .stages[1].status === 'done', 'committing to one action completes the stage');

console.log('\n== CONTAIN auto-skips only when the impact is bounded ==');
const bounded = Pipeline.progress(k => full[k] || '', { severity: '3', blast_radius: 'specific-user' });
ok(bounded.stages[1].status === 'n/a', 'S3 + one tenant, not growing -> n/a (' + bounded.stages[1].reason + ')');
ok(Pipeline.progress(k => full[k] || '',
  { severity: '3', blast_radius: 'specific-user', impact_growing: true }).stages[2].status !== 'n/a',
  'growing impact keeps CONTAIN required');
ok(Pipeline.progress(k => full[k] || '',
  { severity: '1', blast_radius: 'all-users' }).stages[2].status !== 'n/a',
  'S1 across all users keeps CONTAIN required');

console.log('\n== a kill counts once, from either side ==');
ok(Pipeline.progress(k => full[k] || '', {
  hypotheses: [{ hypothesis: 'x', outcome: 'refutes' }],
  loop_log: [{ verdict: 'refutes' }]
}).eliminated === 1, 'the same kill logged as a cause AND a loop pass counts once');
ok(Pipeline.progress(k => full[k] || '', { loop_log: [{ verdict: 'refutes' }] }).eliminated === 1,
  'a loop pass alone still counts the kill');

console.log('\n== loop progress counts candidates, not stages ==');
const hyps = [
  { hypothesis: 'image regression', probability: 0.72, est_mins: 25, risk: 'Low — reversible',
    single_variable_test: 'Build v3 and v4 on hn-01 and hn-04', outcome: 'refutes' },
  { hypothesis: 'agent restart', probability: 0.20, est_mins: 25, risk: 'Low — reversible',
    single_variable_test: 'Restart the agent on hn-04 only', outcome: 'untested' },
  { hypothesis: 'network-wide', probability: 0.08, est_mins: 25, risk: 'Low — reversible',
    single_variable_test: 'openstack port list --device-owner network:distributed', outcome: 'untested' }
];
const lp = Pipeline.progress(k => full[k] || '', { hypotheses: hyps });
ok(lp.eliminated === 1 && lp.surviving === 2, '1 eliminated, 2 surviving');
ok(lp.iteration >= 1, 'the pass counter has moved');

console.log('\n== nextBestAction: shortcuts fire before any scoring ==');
ok(Pipeline.nextBestAction({ duplicate_of: 'INC0009700', hypotheses: hyps }).kind === 'stop',
  'a live duplicate stops the work even with open candidates');
ok(Pipeline.nextBestAction({ verdict: 'works_as_designed', hypotheses: hyps }).kind === 'stop',
  'works-as-designed stops the work even with open candidates');
ok(Pipeline.nextBestAction({ verdict: 'known_error', kb_id: 'KB-2025-0001', hypotheses: hyps }).kind === 'apply',
  'a known error says apply, not test');

const nba = Pipeline.nextBestAction({ hypotheses: hyps });
ok(nba.kind === 'test', 'with nothing known it recommends a single-variable test');
console.log('       -> ' + nba.headline);

const risky = [
  { hypothesis: 'rebuild the cluster', probability: 0.9, est_mins: 20, risk: 'High — irreversible',
    single_variable_test: 'Rebuild the OVN southbound DB' },
  { hypothesis: 'agent restart', probability: 0.2, est_mins: 25, risk: 'Low — reversible',
    single_variable_test: 'Restart the agent on hn-04 only' }
];
ok(/Restart the agent/.test(Pipeline.nextBestAction({ hypotheses: risky }).headline),
  'a reversible 20% test beats an irreversible 90% one');

console.log('\n== the agent plans in stages ==');
return AIAgent.run('action_plan', {
  priority: '2', blast_radius: 'specific-user', site: 'HN',
  service_component: 'nova-metadata',
  deviation: 'Windows VM creation fails in HN',
  error_signature_raw: 'No port found in network None with IP address 169.254.169.254'
}).then(res => {
  ok(res.plan_steps.length === 8, 'the agent returns 8 stages, not 10');
  ok(res.plan_steps.every(s => s.key && Pipeline.byKey(s.key)),
    'every returned stage maps to a pipeline stage');
  ok(res.actions.length === 0,
    'the agent no longer emits a duplicate action list beside the stage cards');
  console.log('       cold start: ' + res.headline);
  res.plan_steps.forEach(s => console.log('       ' + (Pipeline.byKey(s.key).icon) + ' ' +
    s.phase.padEnd(11) + ' ' + s.status.padEnd(12) + (s.shortcut ? '(' + s.shortcut + ')' : '')));

  /* The whole value of the pipeline is what it SKIPS. On a signature the KB
     already knows, NARROW and TEST — the expensive middle — must come back
     already answered, or the time saving is imaginary. */
  return AIAgent.run('action_plan', {
    priority: '2', blast_radius: 'specific-user', site: 'HN',
    customer_id: 'hn-bank', service_component: 'nova-metadata',
    deviation: 'Windows guests unconfigured after agent restart',
    error_signature_raw: 'No port found in network None with IP address 169.254.169.254'
  });
}).then(known => {
  console.log('\n       known signature: ' + known.headline);
  known.plan_steps.forEach(s => console.log('       ' + (Pipeline.byKey(s.key).icon) + ' ' +
    s.phase.padEnd(11) + ' ' + s.status.padEnd(12) + (s.shortcut ? '(' + s.shortcut + ')' : '')));

  const get = k => known.plan_steps.find(s => s.key === k);
  const skippable = ['narrow', 'test'].map(get);
  ok(skippable.every(s => s.status === 'done' || s.status === 'n/a'),
    'a known signature marks NARROW and TEST already answered');
  ok(skippable.every(s => s.est_mins === 0),
    'and charges 0 minutes for them');
  ok(get('confirm').status === 'not-started',
    'but CONFIRM is still required — a known cause is not a proven one here');
  ok(get('impact').status === 'not-started',
    'and IMPACT is never skipped, however well known the cause is');

  console.log('\n' + (fails ? '✗ ' + fails + ' failure(s)' : '✓ all checks passed'));
  process.exit(fails ? 1 : 0);
}).catch(e => { console.error('threw: ' + e.stack); process.exit(1); });
