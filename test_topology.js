#!/usr/bin/env node
/* =============================================================================
 * test_topology.js — checks kt_topology.js against the real seeded data.
 *
 *   node test_topology.js
 *
 * No dependencies: kb_database.js, kt_data.js and kt_topology.js are run in a
 * vm context with a fake `window` and `localStorage`, exactly the two browser
 * globals they touch.
 *
 * What it is actually guarding:
 *   - every open ticket lands on exactly one mapped infra location (a ticket
 *     that falls through to 'unmapped location' is invisible on the mind map);
 *   - mindmap indentation stays monotonic — mermaid's mindmap grammar is
 *     whitespace-significant, and a two-level jump is a parse error;
 *   - the history window is inclusive and its day buckets total the case list,
 *     so the bar chart cannot disagree with the table under it.
 * ========================================================================== */
'use strict';

const fs = require('fs');
const vm = require('vm');
const path = require('path');

const DIR = __dirname;

const store = {};
const sandbox = {
  console,
  localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; }
  },
  Date, Math, JSON, Object, Array, String, Number, Set, Map, isNaN, parseInt, parseFloat
};
sandbox.window = sandbox;
vm.createContext(sandbox);

['kb_database.js', 'kt_data.js', 'kt_topology.js'].forEach(f =>
  vm.runInContext(fs.readFileSync(path.join(DIR, f), 'utf8'), sandbox, { filename: f }));

const { Topology, History, CUSTOMERS, CASE_STORE } = sandbox;

let fails = 0;
const ok = (cond, msg) => {
  console.log((cond ? '  ok   ' : '  FAIL ') + msg);
  if (!cond) fails++;
};

console.log('\n== topology tree ==');
CUSTOMERS.forEach(c => {
  const t = Topology.tree(c.customer_id);
  ok(t !== null, c.customer_id + ' builds');
  const leaves = t.sites.reduce((n, s) =>
    n + s.locations.reduce((m, l) => m + l.issues.length, 0), 0);
  ok(leaves === t.total, c.customer_id + ': ' + leaves + '/' + t.total + ' tickets placed');
  t.sites.forEach(s => s.locations.forEach(l =>
    ok(l.loc.detail !== 'unmapped location',
      c.customer_id + '/' + s.site + '/' + l.loc.id + ' is a mapped location')));
  console.log('       ' + c.name + ': ' + t.total + ' open · ' + t.site_count +
    ' sites · ' + t.location_count + ' locations · ' + t.p1 + ' S1');
});

console.log('\n== locate() is deterministic ==');
ok(CASE_STORE.slice(0, 60).every(c => Topology.locate(c).loc.id === Topology.locate(c).loc.id),
  'the same case always resolves to the same location');

console.log('\n== mermaid source shape ==');
const t0 = Topology.tree('hn-bank');
const mm = Topology.mindmap(t0);
const fc = Topology.flowchart(t0);
ok(mm.startsWith('mindmap\n'), 'mindmap opens with the mindmap keyword');
ok(/^\s{2}root\(\(/m.test(mm), 'mindmap has a root node');
ok(fc.startsWith('flowchart LR'), 'flowchart declares a direction');
ok(fc.includes('classDef s1'), 'flowchart defines the severity classes');
ok(/click \w+ call ktTopoOpenCase\("[A-Za-z0-9._-]+"\)/.test(fc) || t0.total === 0,
  'flowchart click handlers carry sanitized ids');

const indents = mm.split('\n').slice(1).map(l => l.match(/^ */)[0].length);
ok(indents.every(n => n % 2 === 0), 'every mindmap line indents by a multiple of two');
ok(indents.every((n, i) => i === 0 || n - indents[i - 1] <= 2),
  'mindmap indentation never jumps more than one level');

console.log('\n== label sanitising ==');
ok(Topology.mmText('Nasty (title) [with] {brackets} "quotes"') === 'Nasty title with brackets quotes',
  'mmText strips every bracket and quote');
ok(Topology.fcText('He said "boom" #now') === 'He said boom now', 'fcText strips quotes and hashes');
ok(Topology.mmText('x'.repeat(80), 20).length <= 20, 'mmText honours the max length');

console.log('\n== history ==');
[['3d', 3], ['7d', 7], ['30d', 30]].forEach(([key, days]) => {
  const w = History.windowFor(key);
  const r = History.query('hn-bank', w);
  ok(r.window.days === days, key + ' spans ' + days + ' days');
  ok(r.cases.every(c => {
    const t = new Date(c.opened_at.replace(' ', 'T')).getTime();
    return t >= w.from && t <= w.to;
  }), key + ': every case is inside the window');
  ok(r.days.reduce((n, d) => n + d.opened, 0) === r.cases.length,
    key + ': day buckets total the case list (' + r.cases.length + ')');
});

ok(History.customWindow('2026-08-20', '2026-08-01') === null, 'a reversed custom range is rejected');
const good = History.customWindow('2026-08-01', '2026-08-20');
ok(good && good.days === 20, 'a custom range counts inclusive days');
const rc = History.query('hn-bank', good);
const csv = History.csv(rc);
ok(csv.split('\n').length === rc.cases.length + 1, 'CSV is one header plus one row per case');
ok(csv.split('\n')[0].includes('infra_location'), 'CSV carries the resolved infra location');

console.log('\n' + (fails ? '✗ ' + fails + ' failure(s)' : '✓ all checks passed'));
process.exit(fails ? 1 : 0);
