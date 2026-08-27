/* The support tab strip: order, names, and that every tab still opens its pane.
 *
 * Reordering list items is exactly where a tab quietly stops resolving to
 * anything — the label still reads correctly and the click does nothing — so
 * this asserts hrefs and active state as well as labels.
 *
 *   NODE_PATH=<dir containing jsdom> node test_tabs.js
 */
const fs = require('fs');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const file = path.join(__dirname, 'kt_support_demo_v9.html');
let fail = 0;
const check = (n, c, d) => {
  console.log('  ' + (c ? 'PASS' : 'FAIL') + '  ' + n + (c ? '' : '   -> ' + d));
  if (!c) fail++;
};

const vc = new VirtualConsole();
vc.on('jsdomError', e => console.error('  [page error] ' + e.message));
const dom = new JSDOM(fs.readFileSync(file, 'utf8'),
  { runScripts: 'dangerously', pretendToBeVisual: true, url: 'file://' + file, virtualConsole: vc });

setTimeout(() => {
  const doc = dom.window.document;

  // There are two .kt-tabs strips — the customer view and the support view —
  // so every selector here is scoped. An unscoped query silently mixes them
  // and reports "two active tabs" as a bug when it is the correct state.
  const view = doc.getElementById('supportView');
  const tabs = Array.from(view.querySelectorAll('.kt-tabs .nav-link'));
  const names = tabs.map(t => t.querySelector('.tab-label').textContent.trim());

  const cxNames = Array.from(doc.querySelectorAll('#customerView .kt-tabs .tab-label'))
    .map(e => e.textContent.trim());
  console.log('\n=== customer view (untouched) ===');
  cxNames.forEach((n, i) => console.log('  ' + (i + 1) + '. ' + n));
  console.log('\n=== support view ===');

  names.forEach((n, i) => console.log('  ' + (i + 1) + '. ' + n));

  console.log('\n=== checks ===');
  check('New Support Workflow is first', names[0] === 'New Support Workflow', names[0]);
  check('Legacy Ticket DB Integration is second', names[1] === 'Legacy Ticket DB Integration', names[1]);
  check('Ticket status follows them', names[2] === 'Ticket status', names[2]);
  check('old names are gone',
        !names.includes('New Workflow') && !names.includes('Legacy Integration'), names.join(' | '));
  check('no tab appears twice', new Set(names).size === names.length, names.join(' | '));
  check('all 9 support tabs still present', tabs.length === 9, String(tabs.length));
  check('the customer view is untouched',
        cxNames.join('|') === 'New Ticket Wizard|My tickets|History|Service map', cxNames.join('|'));

  // Moving list items is where a tab quietly stops opening anything.
  console.log('\n=== every tab resolves to a pane ===');
  let ok = true, bad = [];
  tabs.forEach((t, i) => {
    const href = t.getAttribute('href');
    if (!href || !doc.querySelector(href)) { ok = false; bad.push(names[i] + ' -> ' + href); }
  });
  check('all hrefs point at an existing pane', ok, bad.join(', '));

  const active = tabs.filter(t => t.classList.contains('active'));
  check('exactly one tab starts active', active.length === 1, String(active.length));
  check('the active tab is Ticket status',
        active.length === 1 && active[0].querySelector('.tab-label').textContent.trim() === 'Ticket status',
        active.length ? active[0].querySelector('.tab-label').textContent.trim() : 'none');

  const activePanes = Array.from(view.querySelectorAll('.tab-content > .tab-pane.active'));
  check('exactly one pane starts active', activePanes.length === 1,
        activePanes.map(p => p.id).join(','));
  check('the active pane matches the active tab',
        activePanes.length === 1 && '#' + activePanes[0].id === active[0].getAttribute('href'),
        activePanes.length ? activePanes[0].id : 'none');

  // Each tab carries a colour accent; two tabs sharing one is a visual bug.
  const accents = tabs.map(t => t.getAttribute('data-tab'));
  check('every tab has a colour accent', accents.every(Boolean), accents.join(','));
  check('accents are distinct', new Set(accents).size === accents.length, accents.join(','));

  console.log('\n' + (fail ? '  ' + fail + ' FAILED\n' : '  ALL PASS\n'));
  process.exit(fail ? 1 : 0);
}, 2500);
