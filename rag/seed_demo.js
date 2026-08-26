#!/usr/bin/env node
/* =============================================================================
 * rag/seed_demo.js — load every demo record in this repo into the RAG store.
 *
 *   node rag/seed_demo.js                    # ingest everything
 *   node rag/seed_demo.js --api http://...   # a different backend
 *   node rag/seed_demo.js --dry-run          # build the documents, post nothing
 *   node rag/seed_demo.js --only kb          # kb | cases | tickets
 *
 * Run automatically by `./start.sh rag` when the store is empty.
 *
 * Why a Node script rather than SQL fixtures: the documents are built by the
 * SAME kt_record.js the browser uses. A fixture file would be a second
 * definition of the record shape, and the day it drifted from the browser's
 * the store would hold two incompatible kinds of document with no way to tell
 * them apart. Here, if the browser's ingest is correct, this one is too.
 *
 * Three sources, three jobs:
 *
 *   KB articles (12)   doc_type 'kb'          the only records that state a
 *                                             VERIFIED FIX. Shared tenant '*'.
 *   Demo tickets (10)  'resolution'/'intake'  full KT records — IS/IS NOT,
 *                                             changes, hypotheses, the lot.
 *   Cases (100+)       'intake'/'resolution'  compact fleet coverage, so
 *                                             "has anyone hit this" has a
 *                                             real corpus to answer from.
 * ========================================================================== */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const DIR = path.join(__dirname, '..');

/* --- args ---------------------------------------------------------------- */
const argv = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = argv.indexOf(name);
  return i !== -1 && argv[i + 1] ? argv[i + 1] : fallback;
};
const API = (arg('--api', process.env.KATS_API_URL || 'http://127.0.0.1:8001')).replace(/\/+$/, '');
const DRY = argv.includes('--dry-run');
const ONLY = arg('--only', '');

/* --- load the browser modules into a sandbox ----------------------------- */
const win = {};
const sandbox = {
  window: win, console,
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  Date, Math, JSON, Object, Array, String, Number, Set, Map, RegExp,
  isNaN, parseInt, parseFloat, Boolean, Error
};
sandbox.global = sandbox;
vm.createContext(sandbox);

['kb_database.js', 'kt_data.js', 'kt_topology.js', 'kt_intake.js', 'kt_record.js',
 'demo_tickets.js'].forEach(f => {
  vm.runInContext(fs.readFileSync(path.join(DIR, f), 'utf8'), sandbox, { filename: f });
});

const Record = win.Record;
const KB_DATABASE = win.KB_DATABASE || [];
const DEMO_TICKETS = win.DEMO_TICKETS || [];
const CASE_STORE = win.CASE_STORE || [];

if (!Record) {
  console.error('kt_record.js did not expose Record — cannot build documents.');
  process.exit(1);
}

/* --- build --------------------------------------------------------------- */
const want = kind => !ONLY || ONLY === kind;
const docs = [];

if (want('kb')) {
  KB_DATABASE.forEach(a => {
    const d = Record.fromKbArticle(a);
    if (d) docs.push(d);
  });
}

if (want('tickets')) {
  DEMO_TICKETS.forEach(t => {
    const f = t.fields || {};
    /* The demo tickets carry structured extras the flat field map cannot
       express. Fold the ones that carry diagnostic signal into the derived
       fields kt_record.js already reads, so the hypotheses and the A/B
       comparison reach the vector instead of being dropped on the floor. */
    const fields = Object.assign({}, f);

    /* demo_tickets.js stores the matrix with short keys (h/s/c/t/e/a/o) while
       the DOM collector returns long ones. Read both rather than picking a
       side: getting this wrong is silent — you get "#1  | #2  | #3" in the
       vector and lose the entire hypothesis list without an error. */
    const H = h => ({
      text:     h.hypothesis || h.h || '',
      supports: h.evidence_supports || h.s || '',
      against:  h.evidence_contradicts || h.c || '',
      test:     h.test || h.t || '',
      expected: h.expected || h.e || '',
      actual:   h.actual || h.a || '',
      outcome:  h.outcome || h.o || ''
    });

    if (Array.isArray(t.hypotheses) && t.hypotheses.length) {
      const hs = t.hypotheses.map(H).filter(h => h.text);
      if (hs.length) {
        /* The outcome is the most useful token in the whole matrix — a
           refuted candidate is a search-space reduction someone already paid
           for, and it is what stops the next engineer re-running that test. */
        fields._hypotheses = hs
          .map((h, i) => '#' + (i + 1) + ' ' + h.text +
            (h.outcome ? ' [' + h.outcome.toUpperCase() + ']' : '') +
            (h.test ? ' — tested by: ' + h.test : '') +
            (h.actual ? ' — observed: ' + h.actual : ''))
          .join('\n');
        fields._evidence_for = hs.map(h => h.supports).filter(Boolean).join(' · ');
        fields._evidence_against = hs.map(h => h.against).filter(Boolean).join(' · ');

        const tested = hs.filter(h => h.outcome);
        if (tested.length) {
          const killed = tested.filter(h => h.outcome === 'refutes').length;
          const proven = tested.find(h => h.outcome === 'supports');
          fields._loop_summary = tested.length + ' candidate(s) tested · ' +
            killed + ' eliminated' +
            (proven ? ' · confirmed: ' + proven.text : '');
        }
      }
    }

    /* The 5-Whys chain, where the demo fills one. It is the only place the
       causal reasoning is written down as a chain rather than a conclusion. */
    if (Array.isArray(t.causalChain) && t.causalChain.length && !fields.chgOther) {
      fields.chgOther = 'Causal chain: ' + t.causalChain
        .map(s => typeof s === 'string' ? s : (s.text || s.why || '')).filter(Boolean)
        .join(' → ');
    }
    if (Array.isArray(t.featureCompare) && t.featureCompare.length && !fields.extent_dist) {
      fields.extent_dist = t.featureCompare
        .map(r => r.name + ': broken "' + r.left + '" vs working "' + r.right + '"').join(' · ');
    }

    const d = Record.toRagDoc({
      ticket_id: fields.ticketNumber || t.id,
      customer_id: fields.customerId,
      opened_at: fields.firstNotice || null,
      status: fields.state || 'In Progress',
      fields: fields
    }, { severity: fields.severity });

    if (d.ticket_id && d.customer_id) docs.push(d);
    else console.warn('  skipped ' + t.id + ' — no ticket number or customer');
  });
}

if (want('cases')) {
  const seen = new Set(docs.map(d => d.ticket_id));
  CASE_STORE.forEach(c => {
    if (seen.has(c.ticket_id)) return;   // a demo ticket already covers it, in full
    const d = Record.fromCase(c);
    if (d && d.customer_id) { docs.push(d); seen.add(d.ticket_id); }
  });
}

/* --- report -------------------------------------------------------------- */
const byType = docs.reduce((a, d) => { a[d.doc_type] = (a[d.doc_type] || 0) + 1; return a; }, {});
const chunks = docs.reduce((n, d) => n + d.chunks.length, 0);

console.log('\n  Built ' + docs.length + ' documents, ' + chunks + ' chunks');
Object.keys(byType).sort().forEach(k => console.log('    ' + k.padEnd(11) + byType[k]));

/* A guard rather than a comment: the shared tenant is for scrubbed KB
   articles only. A ticket written under '*' would be readable by every
   customer, which is the one mistake this schema exists to prevent. */
const leaked = docs.filter(d => d.customer_id === Record.SHARED_TENANT && d.doc_type !== 'kb');
if (leaked.length) {
  console.error('\n  ABORT: ' + leaked.length + ' non-KB document(s) under the shared tenant: ' +
    leaked.map(d => d.ticket_id).join(', '));
  process.exit(1);
}

if (DRY) {
  console.log('\n  --dry-run: nothing posted.');
  const sample = docs.find(d => d.doc_type === 'resolution') || docs[0];
  if (sample) {
    console.log('\n  Sample (' + sample.ticket_id + ', ' + sample.doc_type + '):');
    sample.chunks.forEach(c =>
      console.log('    [' + c.section + '] ' + c.content.replace(/\n/g, ' | ').slice(0, 110) + '…'));
  }
  console.log();
  process.exit(0);
}

/* --- post ---------------------------------------------------------------- */
/* Batched: embedding 300+ chunks is the slow part, and one request per
   document turns a 30-second seed into several minutes of HTTP overhead. */
const BATCH = 10;

async function post(pathname, body) {
  const res = await fetch(API + pathname, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) throw new Error('HTTP ' + res.status + ' ' + (await res.text()).slice(0, 300));
  return res.json();
}

(async () => {
  try {
    const health = await (await fetch(API + '/health')).json();
    console.log('\n  Backend  ' + API);
    console.log('    embeddings ' + health.embed_model + ' (' + health.embed_mode + ')');
    console.log('    llm        ' + health.llm_model +
      (health.ollama ? '' : '  [unreachable — retrieval will still work]'));
    if (health.embed_mode !== 'ollama') {
      console.log('    NOTE: hash fallback in use — retrieval will be keyword-only.');
    }
  } catch (e) {
    console.error('\n  Cannot reach ' + API + ' — ' + e.message);
    console.error('  Start it with:  ./start.sh rag\n');
    process.exit(1);
  }

  console.log('\n  Ingesting (embedding ' + chunks + ' chunks, this takes a moment)…');
  let done = 0, failed = 0;
  for (let i = 0; i < docs.length; i += BATCH) {
    const batch = docs.slice(i, i + BATCH);
    try {
      const r = await post('/tickets/bulk', batch);
      done += r.ingested;
      failed += r.failed;
      (r.errors || []).forEach(e => console.warn('    ! ' + e.ticket_id + ': ' + e.error));
    } catch (e) {
      failed += batch.length;
      console.warn('    ! batch ' + (i / BATCH + 1) + ' failed: ' + e.message);
    }
    process.stdout.write('\r    ' + Math.min(i + BATCH, docs.length) + '/' + docs.length);
  }

  const after = await (await fetch(API + '/health')).json();
  console.log('\n\n  Ingested ' + done + ' documents' + (failed ? ', ' + failed + ' failed' : ''));
  console.log('  Store now holds ' + after.counts.tickets + ' documents, ' +
    after.counts.chunks + ' chunks, ' + after.counts.embedded + ' embedded\n');
  process.exit(failed ? 1 : 0);
})();
