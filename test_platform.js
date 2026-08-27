/* Headless verification of the per-environment health dashboard.
 *
 *   NODE_PATH=<dir with jsdom> node test_platform.js
 *
 * Checks the three gauges render, that the arc length matches the score,
 * that every health deduction names whether it came from the ticket record
 * or from the telemetry stub, and that no environment changes colour as the
 * hours pass. */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = __dirname;
const file = path.join(ROOT, 'kt_support_demo_v9.html');

let fail = 0;
const check = (name, cond, detail) => {
  console.log('  ' + (cond ? 'PASS' : 'FAIL') + '  ' + name + (cond ? '' : '   -> ' + detail));
  if (!cond) fail++;
};

// Surface page-script errors — a silent exception in kt_platform.js would
// otherwise show up only as "Platforms is undefined" three checks later.
const vc = new (require('jsdom').VirtualConsole)();
vc.on('jsdomError', e => console.error('  [page error] ' + e.message));

const dom = new JSDOM(fs.readFileSync(file, 'utf8'), {
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  url: 'file://' + file,
  virtualConsole: vc
});
const w = dom.window;

setTimeout(() => {
  console.log('\n=== module loaded ===');
  check('window.Platforms exists', !!w.Platforms, typeof w.Platforms);
  if (!w.Platforms) { console.log('\n  ' + fail + ' FAILED\n'); process.exit(1); }

  check('three environments defined', w.Platforms.PLATFORMS.length === 3,
        JSON.stringify(w.Platforms.PLATFORMS.map(p => p.key)));
  check('named Flex / OpsC / Flex AI',
        w.Platforms.PLATFORMS.map(p => p.label).join(',') === 'Flex,OpsC,Flex AI',
        w.Platforms.PLATFORMS.map(p => p.label).join(','));

  console.log('\n=== every generated component is mapped ===');
  const unmapped = new Set();
  w.CASE_STORE.forEach(c => {
    if (c.service_component && w.Platforms.of(c) === 'unassigned') unmapped.add(c.service_component);
  });
  check('no unclassified components in the demo corpus', unmapped.size === 0,
        Array.from(unmapped).join(', '));

  console.log('\n=== seeded issues per environment (>= 5 each) ===');
  const seeded = {};
  w.CASE_STORE.filter(c => c._env_seed).forEach(c => {
    seeded[c._env_seed] = (seeded[c._env_seed] || 0) + 1;
  });
  ['flex', 'opsc', 'flexai'].forEach(k => {
    check(k + ' has at least 5 seeded issues (' + seeded[k] + ')', seeded[k] >= 5, String(seeded[k]));
  });
  check('seeded issues land in the right environment',
        w.CASE_STORE.filter(c => c._env_seed).every(c => w.Platforms.of(c) === c._env_seed),
        'a seeded case classified into the wrong platform');

  console.log('\n=== GPU failure modes present on Flex AI ===');
  const gpuCases = w.CASE_STORE.filter(c => w.Platforms.of(c) === 'flexai');
  const sigs = gpuCases.map(c => c.error_signature_raw || '').join(' | ');
  [['Xid fault', /Xid \d+/], ['ECC error', /ECC/], ['NVLink / NCCL', /NCCL/],
   ['thermal cap', /Thermal Slowdown/], ['GPU overload', /GPU_UTIL 100%|queue depth/],
   ['CUDA OOM', /CUDA out of memory/], ['driver mismatch', /CUDA driver version is insufficient/],
   ['unschedulable', /Insufficient nvidia\.com\/gpu/]
  ].forEach(([name, re]) => check(name + ' represented', re.test(sigs), 'not found'));

  console.log('\n=== the three bands all render ===');
  ['healthy', 'degraded', 'at-risk'].forEach(b => {
    const s = w.bandStyle(b);
    check(b + ' -> ' + s.word + ' / ' + s.colour,
          !!s.colour && !!s.word && s.colour !== '#8E8E93', JSON.stringify(s));
  });
  check('the three bands use three distinct colours',
        new Set(['healthy', 'degraded', 'at-risk'].map(b => w.bandStyle(b).colour)).size === 3, '');

  console.log('\n=== per-environment health over 7d ===');
  const rows = w.Platforms.diagnose('7d');
  rows.forEach(r => {
    const t = r.telemetry;
    console.log('  ' + (r.meta.label + '            ').slice(0, 12) +
      String(r.score == null ? '—' : r.score).padStart(4) + '/100  ' +
      (r.band + '        ').slice(0, 9) +
      'cases=' + String(r.counts.total).padStart(3) +
      ' open=' + String(r.counts.open).padStart(3) +
      ' P1=' + r.counts.p1_open +
      (t ? '  peak ' + r.pressure.hottest + ' ' + r.pressure.value + '%' : ''));
    console.log('      driven by: ' + r.driven_by);
    console.log('      ' + r.summary);
  });

  const scored = rows.filter(r => r.score != null);
  check('every environment has cases', scored.every(r => r.counts.total > 0),
        scored.map(r => r.meta.label + '=' + r.counts.total).join(' '));
  check('scores are 0-100', scored.every(r => r.score >= 0 && r.score <= 100), '');
  check('worst environment sorts first',
        scored.length < 2 || scored[0].score <= scored[1].score, '');
  check('a band is assigned to every environment',
        scored.every(r => ['healthy', 'degraded', 'at-risk'].includes(r.band)), '');
  check('telemetry is flagged unmeasured',
        scored.every(r => r.telemetry && r.telemetry.measured === false),
        'a reading claimed to be measured');
  check('every deduction names its source',
        scored.every(r => r.factors.every(f => ['incidents', 'telemetry'].includes(f.source))),
        'a factor with no source');

  console.log('\n=== the band must not change with the hour ===');
  // Telemetry drifts by the hour. If a baseline sits close enough to a
  // threshold that the drift crosses it, the same unchanged fleet renders a
  // different colour depending on when you open the page. Walk all 24 hours.
  const RealDate = w.Date;
  const bandsByHour = {};
  for (let hr = 0; hr < 24; hr++) {
    w.Date = class extends RealDate {
      constructor(...a) { super(...(a.length ? a : [RealDate.now()])); }
      getHours() { return hr; }
      static now() { return RealDate.now(); }
    };
    w.Platforms.PLATFORMS.forEach(p => {
      const h = w.Platforms.health(p.key, '7d');
      (bandsByHour[p.key] = bandsByHour[p.key] || new Set()).add(h.band);
    });
  }
  w.Date = RealDate;
  Object.keys(bandsByHour).forEach(k => {
    const set = Array.from(bandsByHour[k]);
    check(k + ' holds one band across all 24 hours (' + set.join('/') + ')',
          set.length === 1, 'flips between ' + set.join(' and '));
  });

  console.log('\n=== the gauges actually render ===');
  const doc = w.document;
  const row = doc.getElementById('envHealthRow');
  check('#envHealthRow exists', !!row, 'missing');
  if (row) {
    // Force the support view to render the dashboard.
    if (typeof w.renderDashboard === 'function') w.renderDashboard();
    const cards = row.querySelectorAll('.env-card');
    check('one card per environment', cards.length === rows.length,
          cards.length + ' cards for ' + rows.length + ' environments');
    const svgs = row.querySelectorAll('.gauge svg');
    check('a round gauge per card', svgs.length === rows.length, String(svgs.length));

    // The arc must match the score, not just be present.
    const R = 26, C = 2 * Math.PI * R;
    let arcOk = true, arcDetail = '';
    rows.forEach((r, i) => {
      if (r.score == null) return;
      const circles = svgs[i].querySelectorAll('circle');
      if (circles.length < 2) { arcOk = false; arcDetail = r.meta.label + ' has no value arc'; return; }
      const da = circles[1].getAttribute('stroke-dasharray') || '';
      const drawn = parseFloat(da.split(' ')[0]);
      const want = C * r.score / 100;
      if (Math.abs(drawn - want) > 0.1) {
        arcOk = false;
        arcDetail = r.meta.label + ' score ' + r.score + ' drew ' + drawn.toFixed(1) + ', expected ' + want.toFixed(1);
      }
    });
    check('arc length matches the score', arcOk, arcDetail);

    const bands = Array.from(row.querySelectorAll('.env-band')).map(e => e.textContent.trim());
    check('each gauge prints a word, not colour alone',
          bands.length === rows.length && bands.every(b => b.length > 0), bands.join('|'));
    check('resource bars rendered', row.querySelectorAll('.env-metric').length >= 12,
          String(row.querySelectorAll('.env-metric').length));
    check('demo-telemetry caveat is visible',
          /monitoring feed \(demo values\)/.test(row.innerHTML), 'caveat missing from the card');
    check('gauges sit above the fleet KPIs',
          row.compareDocumentPosition(doc.getElementById('kpiRow')) & 4,
          'kpiRow is not after envHealthRow');
  }

  console.log('\n=== KARL diagnoses each environment ===');
  const res = w.AIAgent.run('infra_health', { range: '7d' });
  Promise.resolve(res).then(r => {
    check('agent returns platform breakdown', Array.isArray(r.platforms) && r.platforms.length >= 3,
          JSON.stringify((r.platforms || []).length));
    check('headline names the worst environment when one is unhealthy',
          scored[0].band === 'healthy' || new RegExp(scored[0].meta.label).test(r.headline),
          r.headline);
    check('reasoning covers all three environments',
          ['Flex', 'OpsC', 'Flex AI'].every(l => r.reasoning.some(x => x.indexOf(l) === 0)),
          r.reasoning.filter(x => /^(Flex|OpsC)/.test(x)).length + ' env lines');
    check('demo-telemetry caveat travels with the diagnostic',
          r.reasoning.some(x => /demo values/.test(x)), 'caveat absent from reasoning');
    check('platform evidence chips emitted',
          r.evidence.some(e => e.type === 'platform'), 'no platform evidence');
    console.log('\n  headline: ' + r.headline);
    r.evidence.filter(e => e.type === 'platform').forEach(e =>
      console.log('  · ' + e.id + ' — ' + e.label + '\n      (' + e.why + ')'));
    console.log('\n  actions:');
    r.actions.forEach(a => console.log('    ' + a.seq + '. ' + a.action));

    console.log('\n' + (fail ? '  ' + fail + ' FAILED\n' : '  ALL PASS\n'));
    process.exit(fail ? 1 : 0);
  });
}, 2500);
