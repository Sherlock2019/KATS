/* =============================================================================
 * kt_record.js — the ticket summary record.
 *
 * ONE definition of "the ticket, summarised", rendered in three places:
 *
 *   customer funnel   review step + confirmation   scope 'customer'
 *   support funnel    §10.1, above the details     scope 'full'
 *   RAG ingestion     POST /tickets                Record.toRagDoc()
 *
 * The point of putting it here rather than in the page is that the customer
 * and the support view cannot then disagree about what was reported. Both read
 * this file; neither owns a second copy of the row list.
 *
 * The rows are the Kepner-Tregoe specification grid — WHAT / WHERE / WHEN /
 * EXTENT, each with an IS and an IS NOT — not a flat dump of the form. That is
 * both the correct KT shape and the shape that retrieves well: "what is NOT
 * affected but could have been" is the single most discriminating sentence a
 * customer ever writes, and a flat field list buries it between two node counts.
 *
 * Load AFTER kb_database.js (vocab labels) and kt_intake.js (field list).
 *
 * Exposes: Record
 * ========================================================================== */
(function (global) {
  'use strict';

  const RECORD_SCHEMA_VERSION = '1.0';

  function txt(v) {
    if (v == null) return '';
    return String(v).trim();
  }

  function join(parts, sep) {
    return parts.map(txt).filter(Boolean).join(sep || ' · ');
  }

  /* Controlled-vocabulary keys are stored, not labels — "neutron-ovn" is what
     groups across tickets. Humans and the LLM both want the label. */
  function vocabLabel(kind, key) {
    const k = txt(key);
    if (!k) return '';
    const list = (global.KB_VOCAB && global.KB_VOCAB[kind]) || [];
    const hit = list.find(x => x.key === k);
    return hit ? hit.label : k;
  }

  /* Same idea for the intake <select>s, whose options live in kt_intake.js. */
  function selectLabel(fieldId, key) {
    const k = txt(key);
    if (!k) return '';
    const all = (global.Intake && global.Intake.ALL_FIELDS) || [];
    const f = all.find(x => x.id === fieldId);
    const opt = (f && f.options) ? f.options.find(o => o[0] === k) : null;
    return opt ? opt[1] : k;
  }

  /* ---------------------------------------------------------------------
   * Groups. The order IS the reading order in both funnels, and the group
   * key is the RAG chunk boundary — see toRagDoc().
   * ------------------------------------------------------------------- */
  const GROUPS = [
    { key: 'scope',      icon: '👤', label: 'Who & scope',       scope: 'customer' },
    { key: 'what',       icon: '🎯', label: 'What is wrong',      scope: 'customer' },
    { key: 'where',      icon: '📍', label: 'Where',              scope: 'customer' },
    { key: 'when',       icon: '🕐', label: 'When',               scope: 'customer' },
    { key: 'extent',     icon: '🔎', label: 'Extent — IS / IS NOT', scope: 'customer' },
    { key: 'changes',    icon: '🔄', label: 'What changed',       scope: 'customer' },
    { key: 'evidence',   icon: '📄', label: 'Evidence & history', scope: 'customer' },
    { key: 'access',     icon: '🔑', label: 'Access & constraints', scope: 'customer' },
    { key: 'grading',    icon: '🚦', label: 'Support grading',    scope: 'support' },
    { key: 'analysis',   icon: '🧪', label: 'Analysis',           scope: 'support' },
    { key: 'resolution', icon: '🔧', label: 'Resolution',         scope: 'support' }
  ];

  /* ---------------------------------------------------------------------
   * The customer half. Every getter reads intake field ids only, so this
   * half is complete the moment the portal form is submitted — nothing here
   * waits on support.
   * ------------------------------------------------------------------- */
  const CUSTOMER_ROWS = [
    { key: 'reported_by', group: 'scope', label: 'Reported by', pii: true,
      get: f => join([f.contactName, f.contactDetails], ' — ') },
    { key: 'project', group: 'scope', label: 'Project / tenant',
      get: f => join([f.company, f.custRef && ('ref ' + f.custRef)]) },
    { key: 'environment', group: 'scope', label: 'Environment',
      get: f => vocabLabel('environment', f.env) },
    { key: 'blast_radius', group: 'scope', label: 'Who is affected',
      get: f => selectLabel('blastRadius', f.blastRadius) },
    { key: 'impact_trend', group: 'scope', label: 'Impact trend',
      get: f => selectLabel('impactTrend', f.impactTrend) },
    { key: 'channel', group: 'scope', label: 'Preferred channel', pii: true,
      get: f => f.commChannel },

    { key: 'goal', group: 'what', label: 'What they were trying to do',
      get: f => f.taskGoal },
    { key: 'expected', group: 'what', label: 'What SHOULD happen',
      get: f => f.p1_2 },
    { key: 'deviation', group: 'what', label: 'What actually happens', key_field: true,
      get: f => f.p1_1 },
    { key: 'blocks', group: 'what', label: 'What it blocks',
      get: f => f.p1_3 },
    { key: 'error', group: 'what', label: 'Exact error message', key_field: true, mono: true,
      get: f => f.p1_err_short },
    { key: 'command', group: 'what', label: 'Command / API call', mono: true,
      get: f => f.taskCommand },

    { key: 'component', group: 'where', label: 'Service / component',
      get: f => join([vocabLabel('service_component', f.serviceComponent), f.serviceComponentDetail]) },
    { key: 'category', group: 'where', label: 'Category',
      get: f => vocabLabel('category', f.category) },
    { key: 'site', group: 'where', label: 'Site / region',
      get: f => f.site },
    { key: 'host', group: 'where', label: 'Node / VM',
      get: f => join([f.nodeId && ('node ' + f.nodeId), f.vmId && ('vm ' + f.vmId)]) },
    { key: 'other_sites', group: 'where', label: 'Other sites affected', is_not: true,
      get: f => f.otherSites },

    { key: 'first_notice', group: 'when', label: 'First noticed', key_field: true,
      get: f => f.firstNotice },
    { key: 'last_good', group: 'when', label: 'Last known good', is_not: true,
      get: f => join([f.whenLastKnownGood, f.lastOK]) },
    { key: 'frequency', group: 'when', label: 'How often',
      get: f => f.whenFrequency },
    { key: 'trigger', group: 'when', label: 'Triggering action',
      get: f => f.whenTrigger },
    { key: 'timing_notes', group: 'when', label: 'Timing notes',
      get: f => f.whenNotes },

    { key: 'is', group: 'extent', label: 'IS affected', key_field: true,
      get: f => f.extent_is },
    { key: 'is_not', group: 'extent', label: 'IS NOT affected (but could be)', key_field: true, is_not: true,
      get: f => f.extent_isnot },
    { key: 'distinctions', group: 'extent', label: 'Difference between the two',
      get: f => f.extent_dist },
    { key: 'counts', group: 'extent', label: 'How much',
      get: f => join([
        f.cntNodes && (f.cntNodes + ' nodes'),
        f.cntVms && (f.cntVms + ' VMs'),
        f.cntTenants && (f.cntTenants + ' tenants'),
        f.cntUsers && (f.cntUsers + ' users')
      ]) },
    { key: 'trend_narrative', group: 'extent', label: 'Behaviour over time',
      get: f => f.trendNarrative },

    { key: 'changes', group: 'changes', label: 'Changed just before', key_field: true,
      get: f => f.changes },
    { key: 'maintenance', group: 'changes', label: 'Maintenance / patch window',
      get: f => f.maint },
    { key: 'new_images', group: 'changes', label: 'New images / versions',
      get: f => f.newImages },
    { key: 'other_history', group: 'changes', label: 'Other relevant history',
      get: f => f.chgOther },

    { key: 'error_full', group: 'evidence', label: 'Full error output', mono: true,
      get: f => f.errMsg },
    { key: 'repro', group: 'evidence', label: 'Steps to reproduce', key_field: true,
      get: f => f.repro },
    { key: 'logs', group: 'evidence', label: 'Logs / screenshots',
      get: f => f.logs },
    { key: 'tried', group: 'evidence', label: 'Already tried',
      get: f => f.tried },
    { key: 'results', group: 'evidence', label: 'What that showed',
      get: f => f.results },
    { key: 'cust_workaround', group: 'evidence', label: 'Workaround in place',
      get: f => f.workaround },

    { key: 'access', group: 'access', label: 'Support access', pii: true,
      get: f => f.access },
    { key: 'constraints', group: 'access', label: 'Change constraints / freeze',
      get: f => f.constraints },
    { key: 'access_notes', group: 'access', label: 'Anything else', pii: true,
      get: f => f.accessNotes }
  ];

  /* ---------------------------------------------------------------------
   * The support half. Legitimately empty at intake — the confirmation table
   * shows these rows as "not captured yet" rather than hiding them, because
   * those blanks are exactly what closure fills in.
   * ------------------------------------------------------------------- */
  const SUPPORT_ROWS = [
    { key: 'severity', group: 'grading', label: 'Severity / state',
      get: f => join([f.severity && ('S' + f.severity), f.state]) },
    { key: 'owner', group: 'grading', label: 'Owner',
      get: f => f.ticketOwner },
    { key: 'mitigation', group: 'grading', label: 'Mitigation in place',
      get: f => join([f.tmpFixSteps, f.workaround]) },

    { key: 'hypotheses', group: 'analysis', label: 'Hypotheses',
      get: f => f._hypotheses },
    { key: 'evidence_for', group: 'analysis', label: 'Evidence for',
      get: f => f._evidence_for },
    { key: 'evidence_against', group: 'analysis', label: 'Evidence against', is_not: true,
      get: f => f._evidence_against },
    { key: 'loop', group: 'analysis', label: 'Narrowing loop',
      get: f => f._loop_summary },

    { key: 'root_cause', group: 'resolution', label: 'Most probable cause', key_field: true,
      get: f => f.permRootCause },
    { key: 'verification', group: 'resolution', label: 'Verification',
      get: f => join([f.permFixTest, f.permFixResults], ' → ') },
    { key: 'corrective', group: 'resolution', label: 'Corrective action', key_field: true,
      get: f => f.permFix },
    { key: 'prevention', group: 'resolution', label: 'Prevention',
      get: f => f.permPrevention }
  ];

  /* The support-form element ids this file reads, so the page can collect
     them with one loop instead of naming them a second time. */
  const SUPPORT_FIELD_IDS = [
    'severity', 'state', 'ticketOwner', 'tmpFixSteps', 'workaround',
    'permRootCause', 'permFixTest', 'permFixResults', 'permFix', 'permPrevention'
  ];

  const ALL_ROWS = CUSTOMER_ROWS.concat(SUPPORT_ROWS);

  /* ---------------------------------------------------------------------
   * rows() — the one function every renderer calls.
   * ------------------------------------------------------------------- */
  function rows(fields, scope) {
    const f = fields || {};
    const list = scope === 'customer' ? CUSTOMER_ROWS : ALL_ROWS;
    return list.map(r => {
      let v = '';
      try { v = txt(r.get(f)); } catch (e) { v = ''; }
      return {
        key: r.key, group: r.group, label: r.label, value: v,
        filled: !!v, key_field: !!r.key_field, is_not: !!r.is_not,
        mono: !!r.mono, pii: !!r.pii
      };
    });
  }

  /** Rows bundled under their group, empty groups dropped unless keepEmpty. */
  function groups(fields, scope, opts) {
    const o = opts || {};
    const all = rows(fields, scope);
    return GROUPS
      .filter(g => scope !== 'customer' || g.scope === 'customer')
      .map(g => {
        const gr = all.filter(r => r.group === g.key);
        return {
          key: g.key, label: g.label, icon: g.icon, scope: g.scope,
          rows: o.keepEmpty ? gr : gr.filter(r => r.filled),
          filled: gr.filter(r => r.filled).length,
          total: gr.length
        };
      })
      .filter(g => o.keepEmpty || g.rows.length);
  }

  /** How complete the record is — separate from Intake.quality(), which
      scores only the six things that change how the ticket gets worked. */
  function completeness(fields, scope) {
    const all = rows(fields, scope);
    return { filled: all.filter(r => r.filled).length, total: all.length };
  }

  /* ---------------------------------------------------------------------
   * Rendering. Kept here rather than in the page so the customer table and
   * the support table cannot drift apart in markup either.
   * ------------------------------------------------------------------- */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function cell(r) {
    if (!r.filled) return '<td class="ai-note"><em>not captured yet</em></td>';
    const body = esc(r.value).replace(/\n/g, '<br>');
    return '<td style="font-size:.8rem"' +
      (r.mono ? ' class="font-monospace"' : '') + '>' + body + '</td>';
  }

  /**
   * opts.keepEmpty   show rows with no value (default true — the blanks are
   *                  informative, and hiding them makes two tickets look alike)
   * opts.hidePII     drop contact / access rows (used for anything leaving the
   *                  browser, and for the support-side summary where the
   *                  contact block already sits in §0)
   */
  function tableHTML(fields, scope, opts) {
    const o = opts || {};
    const keepEmpty = o.keepEmpty !== false;
    const gs = groups(fields, scope, { keepEmpty: keepEmpty });
    if (!gs.length) return '<div class="ai-note">Nothing captured yet.</div>';

    return '<table class="table table-sm table-bordered align-middle mb-0 kt-record">' +
      gs.map(g => {
        const rs = o.hidePII ? g.rows.filter(r => !r.pii) : g.rows;
        if (!rs.length) return '';
        return '<tbody>' +
          '<tr><th colspan="2" class="table-light" style="font-size:.78rem">' +
            g.icon + ' ' + esc(g.label) +
            '<span class="ai-note ms-2">' + g.filled + '/' + g.total + '</span>' +
          '</th></tr>' +
          rs.map(r =>
            '<tr>' +
              '<th class="fw-normal" style="width:26%;font-size:.78rem"' +
                (r.key_field ? ' title="key field — drives matching"' : '') + '>' +
                esc(r.label) + (r.key_field ? ' <i class="fa-solid fa-key ai-note"></i>' : '') +
              '</th>' + cell(r) +
            '</tr>').join('') +
        '</tbody>';
      }).join('') +
    '</table>';
  }

  /** Plain text, for the clipboard, a handover, or an LLM prompt. */
  function toText(fields, scope, opts) {
    const o = opts || {};
    return groups(fields, scope, { keepEmpty: false })
      .map(g => {
        const rs = o.hidePII ? g.rows.filter(r => !r.pii) : g.rows;
        if (!rs.length) return '';
        return g.label.toUpperCase() + '\n' +
          rs.map(r => '  ' + r.label + ': ' + r.value.replace(/\n/g, '\n    ')).join('\n');
      })
      .filter(Boolean).join('\n\n');
  }

  /* ---------------------------------------------------------------------
   * toRagDoc() — the ingest payload.
   *
   * Three rules, each of which the naive version gets wrong:
   *
   *   1. Facets are NOT embedded. "cntVms: 12" in a vector is noise that
   *      dilutes the sentences that carry the meaning. They travel as
   *      structured columns and become filters at query time.
   *
   *   2. One chunk per KT group, not one per ticket and not one per field.
   *      A ticket's WHEN block and its error block answer different questions;
   *      a single blob per ticket retrieves badly for both.
   *
   *   3. PII never leaves. Contact name, email/phone and the access notes are
   *      dropped outright — they are useless for retrieval and they are the
   *      one thing that must not cross a tenant boundary. Whatever survives is
   *      run through KB.scrubSecrets() as a second line of defence.
   * ------------------------------------------------------------------- */
  const RAG_GROUPS = ['what', 'where', 'when', 'extent', 'changes', 'evidence',
                      'grading', 'analysis', 'resolution'];

  function scrub(s) {
    const v = txt(s);
    if (!v) return '';
    return (global.KB && global.KB.scrubSecrets) ? global.KB.scrubSecrets(v) : v;
  }

  function normSig(s) {
    const v = txt(s);
    if (!v) return '';
    return (global.KB && global.KB.normalizeErrorSignature)
      ? global.KB.normalizeErrorSignature(v) : v.toLowerCase();
  }

  /**
   * @param sub    an Intake submission (Intake.submit / Intake.byId), or
   *               { ticket_id, customer_id, opened_at, fields } from the
   *               support side.
   * @param extra  { status, severity, doc_type } — support-side overrides.
   */
  function toRagDoc(sub, extra) {
    const s = sub || {};
    const f = s.fields || {};
    const e = extra || {};

    const hasResolution = !!(txt(f.permRootCause) || txt(f.permFix));
    const docType = e.doc_type || (hasResolution ? 'resolution' : 'intake');
    const scope = e.scope || 'full';

    const sig = txt(f.p1_err_short) || txt(f.errMsg) || txt(f.p1_1);

    /* One chunk per group. `content` is what gets embedded, so it is written
       as readable prose with its labels — an embedder does better with
       "IS NOT affected: Linux VMs on the same host" than with a bare value. */
    const chunks = groups(f, scope, { keepEmpty: false })
      .filter(g => RAG_GROUPS.indexOf(g.key) !== -1)
      .map(g => ({
        section: g.key,
        content: scrub(
          g.label + '\n' +
          g.rows.filter(r => !r.pii)
                .map(r => r.label + ': ' + r.value).join('\n')
        )
      }))
      .filter(c => c.content.replace(/\s/g, '').length > 20);

    return {
      schema_version: RECORD_SCHEMA_VERSION,
      ticket_id: s.ticket_id,
      customer_id: s.customer_id,
      doc_type: docType,
      /* Everything raised through this UI is KT-native. The legacy
         importer sets legacy_* on the records it brings in. */
      source_type: e.source_type || 'new_kt',
      opened_at: s.opened_at || null,
      status: e.status || s.status || 'new',
      title: txt(f.p1_1).slice(0, 200) || 'Customer-reported issue',

      /* Filters, never embedded. */
      facets: {
        site: txt(f.site).toUpperCase().split(/[,;/]/)[0].trim() || null,
        service_component: txt(f.serviceComponent) || null,
        category: txt(f.category) || null,
        environment: txt(f.env) || null,
        severity: e.severity != null ? Number(e.severity)
                  : (f.severity ? Number(f.severity) : null),
        blast_radius: txt(f.blastRadius) || null,
        impact_trend: txt(f.impactTrend) || null,
        quality_score: (s.quality && s.quality.score != null) ? s.quality.score : null
      },

      error_signature_raw: scrub(sig).slice(0, 2000),
      error_signature_norm: normSig(sig).slice(0, 2000),

      /* The structured truth, kept whole so the support view can rebuild the
         table from the database without re-deriving anything. PII stripped. */
      fields: (function () {
        const out = {};
        const drop = { contactName: 1, contactDetails: 1, access: 1, accessNotes: 1 };
        Object.keys(f).forEach(k => { if (!drop[k]) out[k] = scrub(f[k]); });
        return out;
      })(),

      /* The rendered summary, so anything downstream can show the same table
         without loading this file. */
      summary: groups(f, scope, { keepEmpty: false })
        .map(g => ({
          group: g.key, label: g.label,
          rows: g.rows.filter(r => !r.pii)
                      .map(r => ({ key: r.key, label: r.label, value: scrub(r.value) }))
        }))
        .filter(g => g.rows.length),

      chunks: chunks
    };
  }

  /* ---------------------------------------------------------------------
   * The other two shapes in this app, converted to the same document.
   *
   * toRagDoc() handles anything with a KT `fields` map — a customer
   * submission or the support form. These two cover what is left, so every
   * record the app holds has ONE call that puts it in the store and nothing
   * downstream has to know which shape it started as.
   * ------------------------------------------------------------------- */

  /** The shared-knowledge tenant. See the comment in rag/db/init.sql. */
  const SHARED_TENANT = '*';

  /**
   * A Case (kt_data.js) — the compact record behind the dashboards. It has no
   * KT fields, only a title and a signature, so it produces one small chunk.
   * That is the point: it answers "has anyone hit this before" cheaply, and
   * it is what gives the store fleet-wide coverage from day one.
   */
  function fromCase(c) {
    if (!c || !c.ticket_id) return null;
    const solved = c.time_to_resolve_mins != null || c.status === 'Closed';
    const cause = txt(c.root_cause_category);

    const facts = [
      'Title: ' + txt(c.title),
      c.error_signature_raw ? 'Error: ' + txt(c.error_signature_raw) : '',
      'Component: ' + vocabLabel('service_component', c.service_component),
      'Category: ' + vocabLabel('category', c.category),
      'Site: ' + txt(c.site) + ' · Environment: ' + vocabLabel('environment', c.environment),
      'Severity: S' + txt(c.priority) + ' · Status: ' + txt(c.status || (solved ? 'Closed' : 'Open')),
      cause ? 'Root cause category: ' + cause : '',
      c.problem_id ? 'Linked problem: ' + txt(c.problem_id) : '',
      c.kb_id ? 'Fix documented in: ' + txt(c.kb_id) : '',
      c.time_to_resolve_mins != null ? 'Resolved in ' + c.time_to_resolve_mins + ' minutes' : ''
    ].filter(Boolean).join('\n');

    return {
      schema_version: RECORD_SCHEMA_VERSION,
      ticket_id: c.ticket_id,
      customer_id: c.customer_id,
      /* A closed case with a coded cause is an answer; anything else is a
         report. Same intake/resolution split as a live ticket. */
      doc_type: (solved && (cause || c.kb_id)) ? 'resolution' : 'intake',
      /* A Case is the historical record — the equivalent of what the CORE
         import produces. Closed with a coded cause or a linked article means
         somebody established it; anything else is a thread a machine read. */
      source_type: (solved && (cause || c.kb_id)) ? 'legacy_verified' : 'legacy_extracted',
      opened_at: c.opened_at || null,
      status: c.status || (solved ? 'Closed' : 'Open'),
      title: txt(c.title).slice(0, 200),
      facets: {
        site: txt(c.site).toUpperCase() || null,
        service_component: txt(c.service_component) || null,
        category: txt(c.category) || null,
        environment: txt(c.environment) || null,
        severity: c.priority ? Number(c.priority) : null,
        blast_radius: null, impact_trend: null, quality_score: null
      },
      error_signature_raw: scrub(c.error_signature_raw).slice(0, 2000),
      error_signature_norm: normSig(c.error_signature_raw).slice(0, 2000),
      fields: {},
      summary: [{ group: 'what', label: 'Case record',
                  rows: [{ key: 'title', label: 'Title', value: txt(c.title) }] }],
      chunks: [{ section: 'what', content: scrub('Case ' + c.ticket_id + '\n' + facts) }]
    };
  }

  /**
   * A KB article (kb_database.js) — the highest-value thing in the store,
   * because it is the only record that states a verified fix.
   *
   * Written under the shared tenant: an article is scrubbed of customer
   * identity before publication and is meant to be found by everyone. Chunked
   * problem / cause / fix rather than as one blob, so "what fixed it" and
   * "does this match my symptoms" retrieve separately.
   */
  function fromKbArticle(a) {
    if (!a || !a.kb_id) return null;
    const verified = !!(a.verification && a.verification.verified);

    const dist = (a.distinctions || [])
      .map(d => d.dimension + ': IS ' + d.is + ' / IS NOT ' + d.is_not).join('\n');

    const chunks = [
      { section: 'what', content:
        'Known issue\nTitle: ' + txt(a.title) +
        (a.error_signature_raw ? '\nError: ' + txt(a.error_signature_raw) : '') +
        (a.issue_description ? '\nSymptoms: ' + txt(a.issue_description) : '') +
        (a.expected_behavior ? '\nExpected: ' + txt(a.expected_behavior) : '') +
        (a.impact ? '\nImpact: ' + txt(a.impact) : '') +
        ((a.symptom_tags || []).length ? '\nSymptom tags: ' + a.symptom_tags.join(', ') : '') },
      dist ? { section: 'extent', content: 'Distinctions — IS / IS NOT\n' + dist } : null,
      { section: 'resolution', content:
        'Verified fix (' + a.kb_id + ')\n' +
        (a.root_cause ? 'Root cause: ' + txt(a.root_cause) + '\n' : '') +
        (a.resolution ? 'Resolution: ' + txt(a.resolution) + '\n' : '') +
        (a.workaround ? 'Workaround: ' + txt(a.workaround) + '\n' : '') +
        (a.rollback ? 'Rollback: ' + txt(a.rollback) + '\n' : '') +
        'Verified: ' + (verified ? 'yes' : 'NO — unverified, treat with caution') +
        (a.reuse_count ? '\nReused ' + a.reuse_count + ' time(s)' : '') +
        (a.time_to_resolve_mins ? '\nTook ' + a.time_to_resolve_mins + ' minutes the first time' : '') }
    ].filter(Boolean).map(c => ({ section: c.section, content: scrub(c.content) }));

    return {
      schema_version: RECORD_SCHEMA_VERSION,
      ticket_id: a.kb_id,
      customer_id: SHARED_TENANT,
      doc_type: 'kb',
      source_type: 'legacy_kb',
      opened_at: a.created_at || null,
      status: verified ? 'verified' : 'unverified',
      title: txt(a.title).slice(0, 200),
      facets: {
        site: txt(a.site).toUpperCase() || null,
        service_component: txt(a.service_component) || null,
        category: txt(a.category) || null,
        environment: txt(a.environment) || null,
        severity: a.priority ? Number(a.priority) : null,
        blast_radius: null, impact_trend: null,
        /* Reused as a confidence signal: a verified article that has been
           applied six times is not the same evidence as an unverified draft. */
        quality_score: verified ? 100 : 50
      },
      error_signature_raw: scrub(a.error_signature_raw).slice(0, 2000),
      error_signature_norm: normSig(a.error_signature_raw).slice(0, 2000),
      fields: {},
      summary: [{ group: 'resolution', label: 'Knowledge base article', rows: [
        { key: 'title', label: 'Title', value: txt(a.title) },
        { key: 'root_cause', label: 'Root cause', value: scrub(a.root_cause) },
        { key: 'resolution', label: 'Resolution', value: scrub(a.resolution) },
        { key: 'verified', label: 'Verified', value: verified ? 'yes' : 'no' }
      ].filter(r => r.value) }],
      chunks: chunks
    };
  }

  global.Record = {
    RECORD_SCHEMA_VERSION, GROUPS, CUSTOMER_ROWS, SUPPORT_ROWS,
    SUPPORT_FIELD_IDS, RAG_GROUPS, SHARED_TENANT,
    rows, groups, completeness, tableHTML, toText,
    toRagDoc, fromCase, fromKbArticle
  };
})(window);
