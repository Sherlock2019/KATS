#!/usr/bin/env node
/* =============================================================================
 * test_record.js — dependency-free checks on the ticket summary record.
 *
 *   node test_record.js
 *
 * kt_record.js is the one place the customer funnel, the support funnel and
 * the RAG store agree about what a ticket says. Two of these checks are the
 * ones worth having: that the customer and support halves stay separated, and
 * that nothing personal or secret survives into the document that leaves the
 * browser. The second is not a style preference — the RAG export is the
 * easiest place in this app to leak a tenant's contact details or a pasted
 * password into a store other engineers can query.
 *
 * Loads the real kb_database.js / kt_data.js / kt_intake.js into a sandbox so
 * vocab keys and select options resolve exactly as they do in the browser.
 * ========================================================================== */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const DIR = __dirname;

const win = {};
const sandbox = {
  window: win, console,
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  Date, Math, JSON, Object, Array, String, Number, Set, Map, RegExp,
  isNaN, parseInt, parseFloat
};
sandbox.global = sandbox;
vm.createContext(sandbox);

['kb_database.js', 'kt_data.js', 'kt_intake.js', 'kt_record.js'].forEach(f => {
  vm.runInContext(fs.readFileSync(path.join(DIR, f), 'utf8'), sandbox, { filename: f });
});

const Record = win.Record;
if (!Record) {
  console.error('kt_record.js did not expose Record');
  process.exit(1);
}

let failures = 0;
function check(name, cond, detail) {
  console.log('  ' + (cond ? '✓' : '✗') + ' ' + name +
    (cond ? '' : '  -> ' + detail));
  if (!cond) failures++;
}

/* A realistic submission, including the two things that must not escape:
   a contact email and a password pasted into the access field. */
const fields = {
  contactName: 'Tran Minh', contactDetails: 'minh@hnbank.example / +84 90 000 0000',
  company: 'core-banking', env: 'production', blastRadius: 'all-users',
  impactTrend: 'growing', commChannel: 'Teams',
  taskGoal: 'Build 12 Windows VMs for the Q3 capacity increase',
  p1_2: 'Instances build and reach the metadata service',
  p1_1: 'Windows instances fail during spawn',
  p1_3: 'Blocks the Q3 capacity increase',
  p1_err_short: 'No port found in network None with IP address 169.254.169.254',
  serviceComponent: 'nova-metadata', category: 'networking', site: 'HN',
  nodeId: 'compute-hn-04', cntVms: '12',
  firstNotice: '2026-08-26 08:15 GMT+7', whenLastKnownGood: '2026-08-25 17:00',
  extent_is: 'Every Windows build tried today at HN',
  extent_isnot: 'Ubuntu instances on the same host build normally; HCMC unaffected',
  changes: 'ovn-metadata-agent restarted in the Tuesday patch window',
  repro: '1. openstack server create --image win2025 ...',
  access: 'Jump host 10.0.0.5, password Hunter2Hunter2, approved by Minh',
  accessNotes: 'ssh root@10.0.0.5'
};

console.log('\nScopes');
const cust = Record.rows(fields, 'customer');
const full = Record.rows(fields, 'full');
check('customer scope has no support rows',
  !cust.some(r => r.key === 'root_cause'), 'root_cause leaked into the customer table');
check('full scope has support rows',
  full.some(r => r.key === 'root_cause'), 'root_cause missing from the support table');
check('unanswered support rows survive as empty',
  full.some(r => r.key === 'corrective' && !r.filled),
  'an empty support row was dropped instead of shown as a gap');

console.log('\nLabels');
const byKey = k => cust.find(r => r.key === k);
check('vocab key resolves to its label',
  byKey('component').value.indexOf('metadata') !== -1, byKey('component').value);
check('select key resolves to its label',
  byKey('blast_radius').value.toLowerCase().indexOf('whole platform') !== -1,
  byKey('blast_radius').value);
check('counts compose', byKey('counts').value === '12 VMs', byKey('counts').value);

console.log('\nRendering');
const html = Record.tableHTML(fields, 'customer', { keepEmpty: true });
check('renders a table', html.indexOf('<table') === 0, html.slice(0, 40));
check('no unescaped markup', !/<script/i.test(html), 'a script tag survived escaping');
check('hidePII drops the contact row',
  Record.tableHTML(fields, 'customer', { hidePII: true }).indexOf('minh@hnbank') === -1,
  'contact email survived hidePII');
check('the customer still sees their own contact row',
  html.indexOf('minh@hnbank') !== -1, 'contact row missing from the customer table');

console.log('\nRAG document');
const doc = Record.toRagDoc({
  ticket_id: 'HNB-HN-0099', customer_id: 'CUST-HNB',
  opened_at: '2026-08-26 08:40', fields: fields, quality: { score: 85 }
});
const serialised = JSON.stringify(doc);

check('intake until a cause or fix exists', doc.doc_type === 'intake', doc.doc_type);
check('facets carry the site', doc.facets.site === 'HN', String(doc.facets.site));
check('facets carry the quality score', doc.facets.quality_score === 85,
  String(doc.facets.quality_score));
check('error signature normalised',
  doc.error_signature_norm.indexOf('169.254.169.254') !== -1 &&
  doc.error_signature_norm.indexOf('None') === -1, doc.error_signature_norm);

check('contact name never leaves the browser',
  serialised.indexOf('Tran Minh') === -1, 'contactName leaked into the RAG document');
check('contact details never leave the browser',
  serialised.indexOf('minh@hnbank') === -1, 'contactDetails leaked into the RAG document');
check('access notes never leave the browser',
  serialised.indexOf('10.0.0.5') === -1, 'the access field leaked into the RAG document');
check('pasted secrets are scrubbed',
  serialised.indexOf('Hunter2Hunter2') === -1, 'a password leaked into the RAG document');

const sections = doc.chunks.map(c => c.section);
check('chunked per KT section, not one blob', doc.chunks.length >= 4, sections.join(','));
check('the extent section is chunked', sections.indexOf('extent') !== -1, sections.join(','));
check('the access section is never chunked', sections.indexOf('access') === -1,
  sections.join(','));
check('IS NOT reaches the embedded text',
  doc.chunks.filter(c => c.section === 'extent')[0].content.indexOf('Ubuntu instances') !== -1,
  'the strongest discriminator is missing from the vector');
check('chunk text keeps its labels',
  doc.chunks.every(c => c.content.indexOf(':') !== -1),
  'a chunk is bare values with no field labels');

console.log('\nLifecycle');
const solved = Record.toRagDoc({
  ticket_id: 'HNB-HN-0099', customer_id: 'CUST-HNB',
  fields: Object.assign({}, fields, {
    permRootCause: 'force_config_drive disabled on the Windows image',
    permFix: 'Restart neutron-ovn-metadata-agent; set force_config_drive=True'
  })
});
check('a cause and a fix flip it to a resolution document',
  solved.doc_type === 'resolution', solved.doc_type);
check('the resolution is chunked separately',
  solved.chunks.some(c => c.section === 'resolution'),
  solved.chunks.map(c => c.section).join(','));

console.log('\n' + (failures === 0
  ? '  All checks passed.\n'
  : '  ' + failures + ' check(s) failed.\n'));
process.exit(failures ? 1 : 0);
