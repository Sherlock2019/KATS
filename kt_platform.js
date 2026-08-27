/* ==========================================================================
 * kt_platform.js — the platform / environment dimension
 *
 * Every case already carries a service_component. It does not carry the
 * PRODUCT the component belongs to, and that is the axis operations actually
 * runs on: "is Flex healthy?" is a different question from "is neutron busy?".
 *
 * Two data sources feed a platform's health, and they are deliberately kept
 * apart because they have different trustworthiness:
 *
 *   incidents  — derived from the ticket store. Real, countable, auditable.
 *   telemetry  — resource utilisation from the monitoring feed. In this build
 *                it is DEMO DATA (see TELEMETRY below). It is labelled as such
 *                everywhere it is displayed, because a fabricated 91% disk
 *                figure that looks measured is worse than no figure at all.
 *
 * KARL's per-environment diagnosis states which of the two moved the score.
 * ========================================================================== */
(function (global) {
  'use strict';

  /* ---------------------------------------------------------------------
   * The three environments
   * ------------------------------------------------------------------- */
  const PLATFORMS = [
    {
      key: 'flex',
      label: 'Flex',
      blurb: 'IaaS fabric — compute, network, storage, images',
      colour: '#0A84FF'
    },
    {
      key: 'opsc',
      label: 'OpsC',
      blurb: 'OpenCenter control plane — API, auth, CLI, dashboard',
      colour: '#1DA54B'
    },
    {
      key: 'flexai',
      label: 'Flex AI',
      blurb: 'GPU / accelerator estate and the model-serving path',
      colour: '#AF52DE'
    }
  ];

  /* ---------------------------------------------------------------------
   * component → platform
   *
   * An explicit table, not a heuristic. A component nobody has classified
   * lands in 'unassigned' and is REPORTED rather than quietly folded into
   * one of the three: a platform total that silently absorbs unknown
   * components is a number you cannot defend in a review.
   * ------------------------------------------------------------------- */
  const COMPONENT_PLATFORM = {
    /* Flex — the IaaS fabric */
    'nova-compute': 'flex',
    'nova-metadata': 'flex',
    'neutron-ovn': 'flex',
    'neutron': 'flex',
    'neutron-dhcp': 'flex',
    'octavia': 'flex',
    'cinder': 'flex',
    'ceph': 'flex',
    'glance': 'flex',
    'wan': 'flex',
    'wan-network': 'flex',
    'controller-os': 'flex',
    'hardware': 'flex',

    /* OpsC — the control plane customers log into */
    'keystone': 'opsc',
    'horizon': 'opsc',
    'opencenter-api': 'opsc',
    'opencenter-cli': 'opsc',
    'mysql-galera': 'opsc',
    'rabbitmq': 'opsc',
    'billing': 'opsc',

    /* Flex AI — accelerators and the serving path */
    'gpu-node': 'flexai',
    'gpu-scheduler': 'flexai',
    'gpu-fabric': 'flexai',
    'gpu-driver': 'flexai',
    'model-serving': 'flexai',
    'vector-store': 'flexai',
    'inference-gateway': 'flexai'
  };

  function platformOf(c) {
    return COMPONENT_PLATFORM[c && c.service_component] || 'unassigned';
  }

  function meta(key) {
    return PLATFORMS.find(p => p.key === key) ||
      { key: 'unassigned', label: 'Unclassified', colour: '#8E8E93',
        blurb: 'components not yet mapped to a platform' };
  }

  /* ---------------------------------------------------------------------
   * Resource telemetry — DEMO DATA
   *
   * A real deployment replaces this function with a call to the monitoring
   * API. It is a function rather than a constant so the swap is a one-place
   * change, and the values move slowly over the day so the demo does not
   * look frozen.
   *
   * `measured: false` travels with every reading and the UI prints it. That
   * flag is the whole point — the moment these numbers are presented as
   * measured, every health score built on them becomes a lie.
   * ------------------------------------------------------------------- */
  /* The three baselines describe three different operating postures, so the
     demo exercises all three health bands rather than showing three green
     gauges. They are also internally consistent with each environment's
     seeded incidents: Flex AI is out of memory AND its open cases are OOM,
     unschedulable pods and index builds that exceed their window. A pretty
     dashboard where the numbers contradict the tickets teaches people to
     distrust both. */
  const TELEMETRY_BASE = {
    flex:   { cpu: 63, memory: 71, storage: 78, network: 44 },  // storage tight
    opsc:   { cpu: 56, memory: 78, storage: 41, network: 22 },  // DB tier tight
    flexai: { cpu: 84, memory: 94, storage: 62, network: 57 }   // memory critical
  };

  /* A slow deterministic drift: same value for everyone looking at the same
     hour, so a screenshot and the live page agree.
     Amplitude is ±3, not ±5, and that is not cosmetic. Each baseline sits far
     enough from its threshold that ±3 cannot cross it, so an environment
     keeps the same health band all day. At ±5 the Flex AI memory baseline
     crossed the 90% critical line and back as the hours passed, and the same
     unchanged fleet rendered orange in the morning and red after lunch — a
     dashboard whose colour depends on when you look at it is worse than one
     with no colour at all. */
  const DRIFT = 3;

  function drift(key, metric) {
    const h = new Date().getHours();
    let s = 0;
    const str = key + metric;
    for (let i = 0; i < str.length; i++) s = (s * 31 + str.charCodeAt(i)) % 997;
    return Math.round(Math.sin((h + s % 24) / 24 * Math.PI * 2) * DRIFT);
  }

  function telemetry(key) {
    const base = TELEMETRY_BASE[key];
    if (!base) return null;
    const out = { measured: false, source: 'monitoring feed (demo values)' };
    Object.keys(base).forEach(m => {
      out[m] = Math.max(0, Math.min(100, base[m] + drift(key, m)));
    });
    return out;
  }

  /* Thresholds a platform engineer would recognise, not round numbers
     chosen to make the demo light up. */
  const PRESSURE = { warn: 75, critical: 90 };

  function pressureOf(t) {
    if (!t) return { band: 'unknown', hottest: null, value: null };
    const metrics = ['cpu', 'memory', 'storage', 'network'];
    let hottest = metrics[0];
    metrics.forEach(m => { if (t[m] > t[hottest]) hottest = m; });
    const v = t[hottest];
    return {
      band: v >= PRESSURE.critical ? 'critical' : v >= PRESSURE.warn ? 'tight' : 'ok',
      hottest, value: v
    };
  }

  /* ---------------------------------------------------------------------
   * Per-platform health
   * ------------------------------------------------------------------- */
  const Platforms = {
    PLATFORMS, COMPONENT_PLATFORM, PRESSURE,
    of: platformOf,
    meta,
    telemetry,

    /** Cases in the window, grouped by platform. */
    group(range) {
      const A = global.Analytics;
      const list = A ? A.inRange(range) : [];
      const g = {};
      PLATFORMS.concat([meta('unassigned')]).forEach(p => { g[p.key] = []; });
      list.forEach(c => {
        const k = platformOf(c);
        (g[k] = g[k] || []).push(c);
      });
      return g;
    },

    /**
     * One environment's health.
     *
     * The score starts at 100 and only ever loses points for something that
     * can be named. Every deduction carries the sentence that justifies it,
     * and each is tagged with its source so the card can show which findings
     * rest on demo telemetry rather than on the ticket record.
     */
    health(key, range) {
      const cases = this.group(range)[key] || [];
      const t = telemetry(key);
      const p = pressureOf(t);
      const days = ((global.Analytics || {}).RANGES || {})[range];
      const windowDays = days ? days.days : 7;

      const open = cases.filter(c => c.time_to_resolve_mins == null);
      const solved = cases.filter(c => c.time_to_resolve_mins != null);
      const p1open = open.filter(c => c.priority === '1');
      const mins = solved.reduce((s, c) => s + c.time_to_resolve_mins, 0);
      const mttr = solved.length ? Math.round(mins / solved.length) : null;

      /* Incident types carried by this platform, most frequent first. */
      const byType = {};
      cases.forEach(c => {
        const k = c.root_cause_category || (c.time_to_resolve_mins == null ? 'under-investigation' : 'unclassified');
        byType[k] = (byType[k] || 0) + 1;
      });
      const types = Object.entries(byType)
        .map(([k, n]) => ({ key: k, count: n }))
        .sort((a, b) => b.count - a.count);

      const byComp = {};
      cases.forEach(c => {
        if (!c.service_component) return;
        byComp[c.service_component] = (byComp[c.service_component] || 0) + 1;
      });
      const components = Object.entries(byComp)
        .map(([k, n]) => ({ key: k, count: n }))
        .sort((a, b) => b.count - a.count);

      let score = 100;
      const factors = [];
      const hit = (pts, why, src) => { score -= pts; factors.push({ pts, why, source: src }); };

      /* An open P1 is a service-down condition on this environment, so one is
         enough to take it out of "healthy" (−14 from 100 lands at 86, and the
         other indicators finish the job) and two are enough to reach at-risk
         once anything else is wrong. An environment carrying two simultaneous
         system-down incidents is not "degraded" in any sense an on-call
         engineer would recognise. Capped at 34 because past three P1s the
         count stops being the useful signal — the score is already as low as
         it needs to be to demand attention. */
      if (p1open.length) {
        hit(Math.min(34, p1open.length * 14),
            p1open.length + ' P1 open on this environment', 'incidents');
      }
      if (windowDays >= 3 && mttr && mttr > 240) {
        hit(10, 'MTTR ' + mttr + ' min against a 240 min target', 'incidents');
      }
      if (cases.length >= 6 && solved.length / cases.length < 0.5) {
        hit(10, Math.round(solved.length / cases.length * 100) +
               '% solved — open work is accumulating here', 'incidents');
      }
      /* One component carrying most of an environment's failures is a
         different problem from an environment that is broadly noisy. */
      if (components[0] && cases.length >= 5 && components[0].count > cases.length * 0.4) {
        hit(8, components[0].key + ' carries ' + components[0].count + ' of ' +
               cases.length + ' cases here', 'incidents');
      }
      // "at or above": the bands are >=, and writing "above the 75% line"
      // next to a reading of exactly 75% is the kind of small inconsistency
      // that makes people stop trusting the rest of the panel.
      if (p.band === 'critical') {
        hit(20, p.hottest + ' at ' + p.value + '% — at or above the ' + PRESSURE.critical +
               '% critical line', 'telemetry');
      } else if (p.band === 'tight') {
        hit(8, p.hottest + ' at ' + p.value + '% — at or above the ' + PRESSURE.warn +
               '% warning line', 'telemetry');
      }

      score = Math.max(0, Math.min(100, score));

      return {
        key, meta: meta(key),
        score,
        band: score >= 80 ? 'healthy' : score >= 55 ? 'degraded' : 'at-risk',
        factors,
        telemetry: t,
        pressure: p,
        window_days: windowDays,
        counts: {
          total: cases.length,
          open: open.length,
          solved: solved.length,
          p1_open: p1open.length,
          mttr,
          customers: new Set(cases.map(c => c.customer_id).filter(Boolean)).size
        },
        types,
        components
      };
    },

    /** Every environment, worst first — the order a dashboard should read in. */
    all(range) {
      const rows = PLATFORMS.map(p => this.health(p.key, range));
      const un = this.group(range).unassigned || [];
      if (un.length) {
        rows.push({
          key: 'unassigned', meta: meta('unassigned'),
          score: null, band: 'unknown', factors: [], telemetry: null,
          pressure: { band: 'unknown', hottest: null, value: null },
          counts: { total: un.length, open: un.filter(c => c.time_to_resolve_mins == null).length,
                    solved: 0, p1_open: 0, mttr: null,
                    customers: new Set(un.map(c => c.customer_id).filter(Boolean)).size },
          types: [], components: []
        });
      }
      return rows.sort((a, b) => {
        if (a.score == null) return 1;
        if (b.score == null) return -1;
        return a.score - b.score;
      });
    },

    /**
     * KARL's plain-language read on each environment.
     *
     * Deliberately says WHICH source drove the verdict. "Flex AI is at risk"
     * is not actionable; "Flex AI is at risk on memory headroom, not on
     * incident volume" tells you whether to call capacity or call support.
     */
    diagnose(range) {
      return this.all(range).map(h => {
        if (h.score == null) {
          return Object.assign({}, h, {
            verdict: 'unclassified',
            summary: h.counts.total + ' case(s) on components not yet mapped to a platform. ' +
                     'Map them in COMPONENT_PLATFORM before trusting the per-environment totals.'
          });
        }

        const fromTelemetry = h.factors.filter(f => f.source === 'telemetry');
        const fromIncidents = h.factors.filter(f => f.source === 'incidents');

        let summary;
        if (!h.factors.length) {
          summary = h.meta.label + ' is inside target on every indicator — ' +
                    h.counts.total + ' case(s), ' + h.counts.open + ' open, ' +
                    (h.pressure.value != null
                      ? 'peak utilisation ' + h.pressure.value + '% on ' + h.pressure.hottest + '.'
                      : 'no telemetry.');
        } else if (fromTelemetry.length && !fromIncidents.length) {
          summary = h.meta.label + ' is carrying no unusual incident load; the score is ' +
                    'resource pressure alone — ' + fromTelemetry[0].why +
                    '. This is a capacity conversation, not a support one.';
        } else if (fromIncidents.length && !fromTelemetry.length) {
          summary = h.meta.label + ' has headroom on every resource; the score is incident ' +
                    'load — ' + fromIncidents[0].why + '.';
        } else {
          summary = h.meta.label + ' is loaded on both axes: ' + fromIncidents[0].why +
                    ', and ' + fromTelemetry[0].why + '. Resource pressure and failure ' +
                    'rate rising together usually means one is causing the other.';
        }

        const topType = h.types[0];
        if (topType && topType.count > 1) {
          summary += ' Most frequent issue type: ' + topType.key +
                     ' (' + topType.count + ' of ' + h.counts.total + ').';
        }

        return Object.assign({}, h, {
          verdict: h.band === 'healthy' ? 'env_healthy'
                 : h.band === 'degraded' ? 'env_degraded' : 'env_at_risk',
          summary,
          driven_by: fromTelemetry.length && fromIncidents.length ? 'both'
                   : fromTelemetry.length ? 'telemetry'
                   : fromIncidents.length ? 'incidents' : 'none'
        });
      });
    }
  };

  global.Platforms = Platforms;
  if (global.KB_VOCAB) {
    global.KB_VOCAB.platform = PLATFORMS.map(p => ({ key: p.key, label: p.label }));
  }
})(window);
