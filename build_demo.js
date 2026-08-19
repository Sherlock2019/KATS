#!/usr/bin/env node
/* =============================================================================
 * build_demo.js — bundle the multi-file app into ONE standalone HTML file.
 *
 *   node build_demo.js
 *
 * Reads  : kt_support_v8.html + kb_database.js + kt_data.js + ai_agent.js
 *          + vendor assets (downloaded once into ./vendor/)
 * Writes : kt_support_demo.html — zero external requests, works offline.
 *
 * Re-run this after editing any source file.
 *
 * First run needs internet to fetch the vendor assets. To prepare them
 * manually (or refresh them), run:
 *
 *   mkdir -p vendor
 *   curl -o vendor/bootstrap.min.css      https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css
 *   curl -o vendor/bootstrap.bundle.min.js https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js
 *   curl -o vendor/fontawesome.min.css    https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css
 *   curl -o vendor/fa-solid-900.woff2     https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/webfonts/fa-solid-900.woff2
 * ========================================================================== */
'use strict';

const fs = require('fs');
const path = require('path');
const https = require('https');

const DIR = __dirname;
const VENDOR = path.join(DIR, 'vendor');

const SOURCE = 'kt_support_v8.html';
const OUTPUT = 'kt_support_demo.html';
// Load order matters: KB layer -> entities -> agent -> demo data
const LOCAL_SCRIPTS = ['kb_database.js', 'kt_data.js', 'ai_agent.js', 'demo_tickets.js'];

const ASSETS = {
  'bootstrap.min.css': 'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css',
  'bootstrap.bundle.min.js': 'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js',
  'fontawesome.min.css': 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css',
  'fa-solid-900.woff2': 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/webfonts/fa-solid-900.woff2'
};

function get(url) {
  return new Promise((resolve, reject) => {
    https.get(url, res => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return get(res.headers.location).then(resolve, reject);
      }
      if (res.statusCode !== 200) return reject(new Error(url + ' -> HTTP ' + res.statusCode));
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve(Buffer.concat(chunks)));
    }).on('error', reject);
  });
}

async function ensureVendor() {
  if (!fs.existsSync(VENDOR)) fs.mkdirSync(VENDOR);
  for (const [name, url] of Object.entries(ASSETS)) {
    const dest = path.join(VENDOR, name);
    if (fs.existsSync(dest) && fs.statSync(dest).size > 0) {
      console.log('  cached  ' + name);
      continue;
    }
    process.stdout.write('  fetch   ' + name + ' … ');
    fs.writeFileSync(dest, await get(url));
    console.log(Math.round(fs.statSync(dest).size / 1024) + ' KB');
  }
}

function build() {
  const read = f => fs.readFileSync(path.join(DIR, f), 'utf8');
  let html = read(SOURCE);

  // Font Awesome — only the solid face is used (48 icons), embed it as a data
  // URI and neutralise the other families so nothing reaches for the network.
  const b64 = fs.readFileSync(path.join(VENDOR, 'fa-solid-900.woff2')).toString('base64');
  let fa = fs.readFileSync(path.join(VENDOR, 'fontawesome.min.css'), 'utf8')
    .replace(/url\(\.\.\/webfonts\/fa-solid-900\.woff2\)/g, 'url(data:font/woff2;base64,' + b64 + ')')
    .replace(/,?url\(\.\.\/webfonts\/[^)]+\)\s*format\("truetype"\)/g, '')
    .replace(/url\(\.\.\/webfonts\/[^)]+\)/g, 'url(data:,)');

  html = html
    .replace(/<link rel="stylesheet" href="https:\/\/cdnjs\.cloudflare\.com[^"]*">/,
      '<style id="fontawesome-inlined">\n' + fa + '\n</style>')
    .replace(/<link rel="stylesheet" href="https:\/\/cdn\.jsdelivr\.net[^"]*">/,
      '<style id="bootstrap-inlined">\n' + fs.readFileSync(path.join(VENDOR, 'bootstrap.min.css'), 'utf8') + '\n</style>')
    .replace(/<script src="https:\/\/cdn\.jsdelivr\.net[^"]*"><\/script>/,
      '<script id="bootstrap-js-inlined">\n' + fs.readFileSync(path.join(VENDOR, 'bootstrap.bundle.min.js'), 'utf8') + '\n</script>');

  LOCAL_SCRIPTS.forEach(f => {
    const src = read(f);
    // A literal </script> inside the source would terminate the wrapper early.
    if (/<\/script/i.test(src)) throw new Error('Cannot inline ' + f + ': it contains a literal </script>');
    const tag = '<script src="' + f + '"></script>';
    if (!html.includes(tag)) throw new Error('Could not find ' + tag + ' in ' + SOURCE);
    html = html.replace(tag, '<script id="' + f.replace(/\./g, '-') + '-inlined">\n' + src + '\n</script>');
  });

  html = html.replace(/<title>[^<]*<\/title>/,
    '<title>KATS — KT AI Enhanced Ticket Support System</title>');

  // Only RESOURCE loads matter — an <a href> to documentation is fine and
  // triggers no request until the reader clicks it.
  const RESOURCE_TAG = /<(?:link|script|img|iframe|source|video|audio|embed|object|track)\b[^>]*?\b(?:src|href|data)="(https?:\/\/[^"]+)"/gi;
  const external = [...html.matchAll(RESOURCE_TAG)].map(m => m[1]);
  const localRefs = [...html.matchAll(/<(?:link|script|img|iframe|source)\b[^>]*?\b(?:src|href)="(?!#|data:|https?:)([^"]+\.(?:js|css|woff2?|ttf|png|jpe?g|svg))"/gi)].map(m => m[1]);
  if (external.length) throw new Error('External resource URLs remain: ' + external.join(', '));
  if (localRefs.length) throw new Error('Local file refs remain: ' + localRefs.join(', '));

  fs.writeFileSync(path.join(DIR, OUTPUT), html);
  console.log('\n  ✓ ' + OUTPUT + '  (' + (html.length / 1048576).toFixed(2) + ' MB, 0 external requests)');
}

(async () => {
  try {
    console.log('Vendor assets:');
    await ensureVendor();
    console.log('\nBundling:');
    build();
  } catch (e) {
    console.error('\n  ✗ build failed: ' + e.message);
    process.exit(1);
  }
})();
