/* =============================================================================
 * kt_intake.js — the contract between the customer portal and the support app.
 *
 * Load AFTER kb_database.js and kt_data.js. Used by BOTH pages, which is the
 * whole point: the field list, the ticket-number scheme and the queue format
 * exist once, so a submission cannot drift out of shape between the page that
 * writes it and the page that reads it.
 *
 *   customer portal  --Intake.submit()-->  localStorage queue + CASE_STORE
 *   support app      --Intake.queue()--->  loads it into the KT form
 *
 * Two things this file deliberately does NOT do:
 *
 *   1. It never exposes another customer's data. The portal has no access to
 *      AIAgent (which returns other tenants' ticket ids during triage), and
 *      knownIssue() returns only the three customer-safe KB fields. Root
 *      cause, resolution steps, kb_id and reuse counts stay internal — they
 *      name hosts, commands and internal processes.
 *
 *   2. It never decides severity or ownership. A customer states impact; the
 *      support side sets severity, owner, team and escalation.
 *
 * Exposes: Intake
 * ========================================================================== */
(function (global) {
  'use strict';

  const QUEUE_KEY = 'kt_intake_queue_v1';
  const SEQ_KEY = 'kt_ticket_seq';        // shared with the support app so
                                          // portal and internal numbers never collide

  /* ---------------------------------------------------------------------
   * The customer-owned fields, grouped the way the portal asks for them.
   * Every `id` is the SAME element id the support form uses, so a submission
   * loads into the KT form with no translation layer.
   * ------------------------------------------------------------------- */
  const STEPS = [
    {
      key: 'contact', n: 1, icon: '👤', label: 'Contact & scope',
      blurb: 'Who you are, which project, and how widely this bites.',
      fields: [
        { id: 'contactName', label: 'Your name', type: 'text', required: true },
        { id: 'contactDetails', label: 'Email / phone / chat', type: 'text', required: true,
          hint: 'How we reach you while we work it.' },
        { id: 'company', label: 'Project / tenant', type: 'text',
          hint: 'e.g. core-banking / proj-rocket' },
        { id: 'custRef', label: 'Your reference / change ID', type: 'text',
          hint: 'Optional — your own ticket or change number.' },
        { id: 'env', label: 'Environment', type: 'vocab', vocab: 'environment', required: true },
        { id: 'blastRadius', label: 'Who is affected?', type: 'select', required: true,
          options: [
            ['all-users', 'Everyone — the whole platform or service'],
            ['external-users', 'Our customers / external users'],
            ['internal-users', 'Our staff, automation or service accounts'],
            ['site-users', 'Everyone at one site or location'],
            ['tenant', 'One tenant or project'],
            ['specific-user', 'One user or workload']
          ],
          hint: 'Naming who IS affected also tells us who is NOT — that is where diagnosis starts.' },
        { id: 'impactTrend', label: 'Is the impact growing?', type: 'select', required: true,
          options: [
            ['growing', 'Growing — still spreading'],
            ['stable', 'Stable — bounded, not spreading'],
            ['shrinking', 'Shrinking — receding on its own']
          ] }
      ]
    },
    {
      key: 'what', n: 2, icon: '🎯', label: 'What is wrong',
      blurb: 'One sentence on what is failing, and what you expected instead.',
      fields: [
        { id: 'taskGoal', label: 'What were you trying to achieve?', type: 'area', rows: 2,
          hint: 'The goal, not the error. "Build 12 Windows VMs for the Q3 capacity increase."' },
        { id: 'p1_2', label: 'What SHOULD happen?', type: 'area', rows: 2, required: true },
        { id: 'p1_1', label: 'What actually happens?', type: 'area', rows: 2, required: true,
          hint: 'One sentence. Name the thing and the defect — leave theories for later.' },
        { id: 'p1_3', label: 'What can you not do because of it?', type: 'area', rows: 2, required: true,
          hint: 'The business impact. This is what sets priority.' },
        { id: 'taskCommand', label: 'Command or API call used', type: 'area', rows: 2, mono: true },
        { id: 'p1_err_short', label: 'The exact error message', type: 'area', rows: 2, mono: true,
          required: true, key_field: true,
          hint: 'Copy/paste it verbatim. This one field is what lets us match your issue to a fix we already have.' }
      ]
    },
    {
      key: 'where', n: 3, icon: '📍', label: 'Where & when',
      blurb: 'Which part of the platform, and the timeline.',
      fields: [
        { id: 'serviceComponent', label: 'Service / component', type: 'vocab', vocab: 'service_component' },
        { id: 'category', label: 'Category', type: 'vocab', vocab: 'category' },
        { id: 'site', label: 'Site / region', type: 'text', required: true, hint: 'HN, HCMC, DC1 …' },
        { id: 'nodeId', label: 'Node / host (if known)', type: 'text' },
        { id: 'vmId', label: 'VM / instance ID (if known)', type: 'text' },
        { id: 'serviceComponentDetail', label: 'Component detail', type: 'text' },
        { id: 'cntNodes', label: 'Nodes affected', type: 'number' },
        { id: 'cntVms', label: 'VMs affected', type: 'number' },
        { id: 'cntTenants', label: 'Tenants affected', type: 'number' },
        { id: 'cntUsers', label: 'Users affected', type: 'number' },
        { id: 'firstNotice', label: 'First noticed (with timezone)', type: 'text', required: true,
          hint: 'e.g. 2026-08-17 09:12 GMT+7' },
        { id: 'whenLastKnownGood', label: 'Last known good', type: 'text',
          hint: 'The last time it definitely worked. Brackets the change that broke it.' },
        { id: 'whenFrequency', label: 'How often?', type: 'text',
          hint: 'Always / intermittent / only under load / only at 09:00 …' },
        { id: 'whenTrigger', label: 'Triggering action (if known)', type: 'text' },
        { id: 'whenNotes', label: 'Timing notes', type: 'area', rows: 2 }
      ]
    },
    {
      key: 'works', n: 4, icon: '🔎', label: 'What still works',
      blurb: 'The most valuable part of this form. A cause has to explain why ' +
             'the broken case fails AND why the working case does not.',
      fields: [
        { id: 'extent_is', label: 'What IS affected — how much, and is it spreading?', type: 'area', rows: 2,
          required: true },
        { id: 'extent_isnot', label: 'What is NOT affected but could have been?', type: 'area', rows: 2,
          required: true, key_field: true,
          hint: 'Same feature working on another node, site, image, tenant or user. ' +
                'The healthy comparison is usually more informative than the failure.' },
        { id: 'extent_dist', label: 'What is different between the two?', type: 'area', rows: 2 },
        { id: 'otherSites', label: 'Other sites affected (if any)', type: 'text' },
        { id: 'trendNarrative', label: 'How does it behave over time?', type: 'area', rows: 2 }
      ]
    },
    {
      key: 'evidence', n: 5, icon: '📄', label: 'Evidence & history',
      blurb: 'Logs, how to reproduce it, what changed, and what you already tried.',
      fields: [
        { id: 'errMsg', label: 'Full error output', type: 'area', rows: 4, mono: true },
        { id: 'repro', label: 'Steps to reproduce', type: 'area', rows: 4, key_field: true,
          hint: 'Numbered. If we can reproduce it, we can test a fix against it.' },
        { id: 'logs', label: 'Log / screenshot locations', type: 'area', rows: 2 },
        { id: 'lastOK', label: 'Last time it worked normally', type: 'text' },
        { id: 'changes', label: 'What changed just before it started?', type: 'area', rows: 2,
          key_field: true,
          hint: 'Patch, deploy, config edit, new image, scale event — anything at all.' },
        { id: 'maint', label: 'Was there a maintenance or patch window?', type: 'area', rows: 2 },
        { id: 'newImages', label: 'New images / templates / versions', type: 'area', rows: 2 },
        { id: 'chgOther', label: 'Other relevant history', type: 'area', rows: 2 },
        { id: 'tried', label: 'What have you already tried?', type: 'area', rows: 3,
          hint: 'So we do not repeat it.' },
        { id: 'results', label: 'What did those attempts show?', type: 'area', rows: 3 },
        { id: 'workaround', label: 'Any workaround currently in place?', type: 'area', rows: 2 }
      ]
    },
    {
      key: 'access', n: 6, icon: '🔑', label: 'Access & submit',
      blurb: 'How we get in, what we must not touch, and where to reach you.',
      fields: [
        { id: 'access', label: 'Can support access the environment?', type: 'area', rows: 3,
          hint: 'Do not paste passwords or private keys here — describe the path and who approves it.' },
        { id: 'constraints', label: 'Change constraints / freeze windows', type: 'area', rows: 2 },
        { id: 'commChannel', label: 'Preferred communication channel', type: 'text' },
        { id: 'accessNotes', label: 'Anything else we should know', type: 'area', rows: 2 }
      ]
    }
  ];

  const ALL_FIELDS = STEPS.reduce((a, s) => a.concat(s.fields), []);
  const FIELD_IDS = ALL_FIELDS.map(f => f.id);

  /* ---------------------------------------------------------------------
   * Ticket quality.
   *
   * Six things that measurably change how the ticket is worked, each stated
   * as what it UNLOCKS rather than as a score to game. Deliberately not
   * phrased as a time saving — we would be inventing a number.
   * ------------------------------------------------------------------- */
  const QUALITY = [
    { key: 'error', weight: 25, label: 'Exact error message',
      unlocks: 'Matches your issue against fixes we already have, instantly.',
      test: f => !!(f.p1_err_short || f.errMsg) },
    { key: 'isnot', weight: 25, label: 'A comparable case that works',
      unlocks: 'The single strongest clue: a cause must explain why that one is fine.',
      test: f => !!(f.extent_isnot || f.featureCompareRight) },
    { key: 'changed', weight: 15, label: 'What changed beforehand',
      unlocks: 'Most faults are a change. This is where we look first.',
      test: f => !!(f.changes || f.maint || f.newImages) },
    { key: 'repro', weight: 15, label: 'Steps to reproduce',
      unlocks: 'Lets us prove a fix works instead of hoping it does.',
      test: f => !!f.repro },
    { key: 'when', weight: 10, label: 'When it started / last worked',
      unlocks: 'Brackets the window a change has to fall inside.',
      test: f => !!(f.firstNotice && (f.whenLastKnownGood || f.lastOK)) },
    { key: 'impact', weight: 10, label: 'What it blocks',
      unlocks: 'Sets how fast this is picked up, and what we protect first.',
      test: f => !!(f.p1_3 && f.blastRadius) }
  ];

  function quality(fields) {
    const f = fields || {};
    const items = QUALITY.map(q => ({
      key: q.key, label: q.label, unlocks: q.unlocks, weight: q.weight, ok: !!q.test(f)
    }));
    const score = items.reduce((t, i) => t + (i.ok ? i.weight : 0), 0);
    return {
      score,
      band: score >= 85 ? 'strong' : score >= 55 ? 'workable' : 'thin',
      items,
      missing: items.filter(i => !i.ok)
    };
  }

  /** Required fields still blank, per step. Drives the stepper's warnings. */
  function missingRequired(fields) {
    const f = fields || {};
    const out = {};
    STEPS.forEach(s => {
      const miss = s.fields.filter(x => x.required && !String(f[x.id] || '').trim());
      if (miss.length) out[s.key] = miss.map(x => x.label);
    });
    return out;
  }

  /* ---------------------------------------------------------------------
   * Customer-safe known-issue lookup.
   *
   * Only these three article fields ever cross to the customer. root_cause
   * and resolution name hosts, config files and shell commands; kb_id and
   * reuse_count are internal bookkeeping that invites "why is this the
   * seventh time" before support has the context to answer it.
   * ------------------------------------------------------------------- */
  const SAFE_KB_FIELDS = ['title', 'issue_description', 'workaround'];

  function knownIssue(fields) {
    const f = fields || {};
    if (!global.KB) return null;
    const sig = f.p1_err_short || f.errMsg || '';
    const query = [f.p1_1, f.p1_err_short, f.errMsg].filter(Boolean).join(' ');
    if (!query || query.length < 12) return null;

    const hits = global.KB.search(query, {
      errorSignature: sig,
      facets: { service_component: f.serviceComponent, environment: f.env },
      limit: 1
    });
    if (!hits.length || hits[0].confidence < 0.35) return null;

    const a = hits[0].article;
    const safe = {};
    SAFE_KB_FIELDS.forEach(k => { if (a[k]) safe[k] = a[k]; });
    safe.confidence = hits[0].confidence;
    safe.has_workaround = !!a.workaround;
    return safe;
  }

  /** This customer's own earlier cases with the same signature. Never anyone else's. */
  function ownHistory(customerId, fields) {
    if (!global.Cases || !customerId) return [];
    const sig = (global.KB && global.KB.normalizeErrorSignature)
      ? global.KB.normalizeErrorSignature((fields || {}).p1_err_short || (fields || {}).errMsg || '')
      : '';
    if (!sig) return [];
    return global.Cases.byCustomer(customerId)
      .filter(c => c.error_signature_norm &&
        (c.error_signature_norm === sig ||
         c.error_signature_norm.includes(sig) || sig.includes(c.error_signature_norm)))
      .sort((a, b) => String(b.opened_at).localeCompare(String(a.opened_at)))
      .slice(0, 5)
      .map(c => ({
        ticket_id: c.ticket_id, opened_at: c.opened_at, title: c.title,
        status: c.status, resolved_mins: c.time_to_resolve_mins
      }));
  }

  /* ---------------------------------------------------------------------
   * This customer's own tickets — for the portal's "My tickets" view.
   * Every query here is scoped by customer_id at the source; there is no
   * code path in this file that can return another tenant's case.
   * ------------------------------------------------------------------- */
  function myTickets(customerId, opts) {
    const o = opts || {};
    if (!global.Cases || !customerId) return [];
    let list = global.Cases.byCustomer(customerId);
    if (o.openOnly) list = list.filter(c => c.status !== 'Closed');
    return list
      .sort((a, b) => String(b.opened_at).localeCompare(String(a.opened_at)))
      .slice(0, o.limit || 500);
  }

  function myStats(customerId) {
    const all = myTickets(customerId);
    const open = all.filter(c => c.status !== 'Closed');
    const closed = all.filter(c => c.time_to_resolve_mins != null);
    const cust = global.Customers ? global.Customers.byId(customerId) : null;
    return {
      customer: cust,
      total: all.length,
      open: open.length,
      closed: closed.length,
      p1_open: open.filter(c => String(c.priority) === '1').length,
      mttr: closed.length
        ? Math.round(closed.reduce((s, c) => s + c.time_to_resolve_mins, 0) / closed.length) : null,
      sla_resolve: cust ? cust.sla_resolve_mins : null,
      sla_response: cust ? cust.sla_response_mins : null
    };
  }

  /* ---------------------------------------------------------------------
   * assist() — the AI agent, seen from the customer's side.
   *
   * It runs the SAME mock agent the support view runs, then strips
   * everything that is not this customer's to see. That filter is the whole
   * function: raw triage answers name other tenants' live tickets by id
   * ("INC0009701 · HCMC-Commerce Cloud"), and internal record ids invite
   * questions support has no context to answer yet.
   *
   * Returns a promise. Resolves to null when the agent is unavailable.
   * ------------------------------------------------------------------- */
  function assist(customerId, fields) {
    const f = fields || {};
    if (!global.AIAgent) return Promise.resolve(null);
    const sig = f.p1_err_short || f.errMsg || f.p1_1 || '';
    if (!sig || sig.length < 12) return Promise.resolve(null);

    const ctx = {
      customer_id: customerId,
      service_component: f.serviceComponent,
      environment: f.env,
      site: f.site,
      priority: '3',
      blast_radius: f.blastRadius,
      deviation: f.p1_1,
      error_signature_raw: sig,
      query: [f.p1_1, f.p1_err_short, f.errMsg].filter(Boolean).join(' ')
    };

    return global.AIAgent.run('triage', ctx).then(r => {
      const mine = id => {
        const c = global.Cases ? global.Cases.byId(id) : null;
        return !!c && c.customer_id === customerId;
      };

      const ownOpen = (r.duplicates_open || []).filter(d => mine(d.ticket_id));
      const ownSolved = (r.duplicates_closed || []).filter(d => mine(d.ticket_id));

      const kbEv = (r.evidence || []).find(e => e.type === 'kb');
      const kb = kbEv && global.KB_DATABASE
        ? global.KB_DATABASE.find(a => a.kb_id === kbEv.id) : null;
      const known = kb ? { title: kb.title, workaround: kb.workaround || '' } : null;

      const probEv = (r.evidence || []).find(e => e.type === 'problem');
      const problem = probEv && global.Problems ? global.Problems.byId(probEv.id) : null;

      const q = quality(f);
      const cust = global.Customers ? global.Customers.byId(customerId) : null;

      let headline;
      if (r.verdict === 'works_as_designed') {
        headline = 'This looks like documented platform behaviour rather than a fault. ' +
          'We will confirm and explain — you may not need a fix at all.';
      } else if (ownOpen.length) {
        headline = 'You already have ' + ownOpen.length + ' open ticket' +
          (ownOpen.length === 1 ? '' : 's') + ' that look' + (ownOpen.length === 1 ? 's' : '') +
          ' like this one. We can add this to it instead of opening a second.';
      } else if (known) {
        headline = 'We recognise this. There is a known workaround you can apply right now, ' +
          'and raising the ticket is what keeps the permanent fix prioritised.';
      } else if (ownSolved.length) {
        headline = 'You have hit this before and we resolved it — we will start from what worked then.';
      } else {
        headline = 'This does not match anything we have seen on your account. ' +
          'The details below are what will let us start quickly.';
      }

      return {
        verdict: r.verdict,
        confidence: r.confidence,
        headline: headline,
        known: known,
        /* Only this customer's tickets, and only the fields they own. */
        own_open: ownOpen.map(d => ({ ticket_id: d.ticket_id, status: d.status, age: d.age })),
        own_solved: ownSolved.slice(0, 3).map(d => ({ ticket_id: d.ticket_id, age: d.age, ttr: d.ttr })),
        /* The FACT of a tracked problem and a target date is reassuring; the
           internal record id is not ours to hand out. */
        tracked: !!problem,
        scheduled_fix: problem ? problem.target_fix_date : null,
        missing: q.missing.map(m => ({ label: m.label, unlocks: m.unlocks })),
        quality: q.score,
        sla_response_mins: cust ? cust.sla_response_mins : null,
        model: (global.AIAgent.CONFIG && global.AIAgent.CONFIG.model) ||
               'kt-support-agent (mock, no model called)'
      };
    }).catch(() => null);
  }

  /* ---------------------------------------------------------------------
   * Ticket numbering — the same CUSTOMER-LOCATION-NUMBER scheme and the same
   * sequence counter the support app uses, so the two never hand out the
   * same number.
   * ------------------------------------------------------------------- */
  function nextSeq() {
    let seq = 1;
    try { seq = (parseInt(localStorage.getItem(SEQ_KEY) || '0', 10) || 0) + 1; } catch (e) {}
    try { localStorage.setItem(SEQ_KEY, String(seq)); } catch (e) {}
    return seq;
  }

  function ticketNumber(customerId, site) {
    const c = global.Customers ? global.Customers.byId(customerId) : null;
    const code = (c && c.code) ? c.code : 'NEW';
    const loc = String(site || '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6) || 'XX';
    return code + '-' + loc + '-' + String(nextSeq()).padStart(4, '0');
  }

  /* ---------------------------------------------------------------------
   * The queue. Same-origin localStorage is what carries a submission from
   * the portal to the support app; serve both over the same host and the
   * hand-off needs no backend.
   * ------------------------------------------------------------------- */
  function readQueue() {
    try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || '[]') || []; }
    catch (e) { return []; }
  }

  function writeQueue(q) {
    try { localStorage.setItem(QUEUE_KEY, JSON.stringify(q)); return true; }
    catch (e) { return false; }
  }

  /**
   * Register the submission as a real Case so it appears immediately in the
   * support app's dashboard, Customer 360, topology map and ticket history —
   * not only in the intake queue.
   *
   * Severity is NOT taken from the customer. They state impact and blast
   * radius; support grades it. Until then it sits at S3 with the customer's
   * own words attached.
   */
  function toCase(sub) {
    const f = sub.fields;
    return {
      ticket_id: sub.ticket_id,
      customer_id: sub.customer_id,
      opened_at: sub.opened_at,
      time_to_resolve_mins: null,
      service_component: f.serviceComponent || 'other',
      category: f.category || 'configuration',
      environment: f.env || 'production',
      site: (f.site || '').toUpperCase().split(/[,;/]/)[0].trim() || 'HN',
      priority: '3',
      root_cause_category: null,
      problem_id: null,
      kb_id: null,
      title: (f.p1_1 || 'Customer-reported issue').slice(0, 120),
      error_signature_raw: f.p1_err_short || f.errMsg || f.p1_1 || '',
      _customer_submitted: true
    };
  }

  function submit(customerId, fields, extras) {
    const f = Object.assign({}, fields);
    const site = (f.site || '').toUpperCase().split(/[,;/]/)[0].trim();
    const now = new Date();
    const p = n => String(n).padStart(2, '0');
    const opened = now.getFullYear() + '-' + p(now.getMonth() + 1) + '-' + p(now.getDate()) +
      ' ' + p(now.getHours()) + ':' + p(now.getMinutes());

    const sub = {
      ticket_id: ticketNumber(customerId, site),
      customer_id: customerId,
      opened_at: opened,
      submitted_at: now.toISOString(),
      status: 'new',
      contact: f.contactName || '',
      channel: f.commChannel || '',
      fields: f,
      featureCompare: (extras && extras.featureCompare) || [],
      impacted: (extras && extras.impacted) || { nodes: [], vms: [] },
      quality: quality(f)
    };

    // Case store first — that is what the support app's views read.
    if (global.Cases && global.Cases.ingest) {
      const c = global.Cases.ingest(toCase(sub));
      c.events = (c.events || []).concat({
        at: opened, type: 'customer_submitted',
        detail: 'Raised by ' + (sub.contact || 'the customer') + ' through the customer portal'
      });
      if (global.Cases.add) global.Cases.add(c);
    }

    const q = readQueue();
    q.unshift(sub);
    const saved = writeQueue(q);
    sub._persisted = saved;
    return sub;
  }

  /** Newest first. `onlyNew` hides submissions already pulled into the form. */
  function queue(onlyNew) {
    const q = readQueue();
    return onlyNew ? q.filter(s => s.status === 'new') : q;
  }

  function byId(ticketId) {
    return readQueue().find(s => s.ticket_id === ticketId) || null;
  }

  function markLoaded(ticketId) {
    const q = readQueue();
    const s = q.find(x => x.ticket_id === ticketId);
    if (!s) return null;
    s.status = 'loaded';
    s.loaded_at = new Date().toISOString();
    writeQueue(q);
    return s;
  }

  function clearQueue() { writeQueue([]); }

  global.Intake = {
    STEPS, ALL_FIELDS, FIELD_IDS, QUALITY, SAFE_KB_FIELDS, QUEUE_KEY,
    quality, missingRequired, knownIssue, ownHistory,
    myTickets, myStats, assist,
    ticketNumber, submit, queue, byId, markLoaded, clearQueue, toCase
  };
})(window);
