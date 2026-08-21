/* =============================================================================
 * kt_topology.js — infrastructure topology + customer ticket history
 *
 * Load AFTER kt_data.js. Adds the one dimension the case model was missing:
 * WHERE, physically, a ticket lives.
 *
 *   Customer  ->  Site  ->  Infra location  ->  Issue
 *
 * A case already carries `site` and `service_component`. Neither answers
 * "which rack / pool / control trio is this landing on", which is the level an
 * engineer actually walks to. INFRA_CLASS maps a component to an infra class,
 * SITE_INFRA lists the real locations of that class at that site, and the
 * ticket id picks one deterministically — so the same ticket always resolves
 * to the same place, reload after reload, with no stored field.
 *
 * Exposes: Topology (tree + mermaid emitters), History (windowed case query)
 * ========================================================================== */
(function (global) {
  'use strict';

  /* ---------------------------------------------------------------------
   * Component -> infra class. Anything unmapped falls back to the control
   * plane, which is where an unclassified service actually runs.
   * ------------------------------------------------------------------- */
  const INFRA_CLASS = {
    'nova-compute': 'compute',
    'nova-metadata': 'compute',
    'neutron-ovn': 'network',
    'neutron-dhcp': 'network',
    'octavia': 'network',
    'wan-network': 'edge',
    'ceph': 'storage',
    'cinder': 'storage',
    'glance': 'storage',
    'keystone': 'control',
    'horizon': 'control',
    'controller-os': 'control',
    'mysql-galera': 'control',
    'rabbitmq': 'control'
  };

  const CLASS_META = {
    compute: { label: 'Compute', icon: 'fa-server', color: '#0d6efd' },
    network: { label: 'Network', icon: 'fa-network-wired', color: '#6f42c1' },
    storage: { label: 'Storage', icon: 'fa-database', color: '#20c997' },
    control: { label: 'Control plane', icon: 'fa-sliders', color: '#fd7e14' },
    edge: { label: 'WAN edge', icon: 'fa-tower-broadcast', color: '#d63384' }
  };

  /* Labels avoid parentheses on purpose — mermaid's mindmap grammar treats
     brackets as node shapes, and the sanitizer would strip them anyway. */
  const SITE_META = {
    HN: { label: 'HN · Hanoi', detail: 'primary north DC' },
    HCMC: { label: 'HCMC · Ho Chi Minh City', detail: 'primary south DC' },
    DC1: { label: 'DC1 · Datacenter 1', detail: 'shared platform DC' },
    DC2: { label: 'DC2 · Datacenter 2', detail: 'shared platform DC' }
  };

  /* ---------------------------------------------------------------------
   * Physical locations per site. `id` is what an engineer says out loud;
   * `detail` is what they will find when they get there.
   * ------------------------------------------------------------------- */
  const SITE_INFRA = {
    HN: {
      compute: [
        { id: 'HN-POD-A', label: 'Compute Pod A', detail: 'racks A1-A6 · 48 hypervisors' },
        { id: 'HN-POD-B', label: 'Compute Pod B', detail: 'racks B1-B6 · 40 hypervisors' }
      ],
      network: [
        { id: 'HN-FAB', label: 'Spine-Leaf Fabric', detail: '2 spine · 8 leaf · OVN overlay' }
      ],
      storage: [
        { id: 'HN-CEPH-SSD', label: 'Ceph SSD Pool', detail: '36 OSD · NVMe tier' },
        { id: 'HN-CEPH-HDD', label: 'Ceph HDD Pool', detail: '72 OSD · capacity tier' }
      ],
      control: [
        { id: 'HN-CTRL', label: 'Control Trio', detail: 'ctrl-hn-01..03 · HA' }
      ],
      edge: [
        { id: 'HN-EDGE', label: 'WAN Edge', detail: 'PE pair · LAG to HCMC' }
      ]
    },
    HCMC: {
      compute: [
        { id: 'HCM-POD-A', label: 'Compute Pod A', detail: 'racks C1-C4 · 32 hypervisors' }
      ],
      network: [
        { id: 'HCM-FAB', label: 'Spine-Leaf Fabric', detail: '2 spine · 6 leaf · OVN overlay' }
      ],
      storage: [
        { id: 'HCM-CEPH', label: 'Ceph Mixed Pool', detail: '48 OSD · SSD+HDD' }
      ],
      control: [
        { id: 'HCM-CTRL', label: 'Control Trio', detail: 'ctrl-hcm-01..03 · HA' }
      ],
      edge: [
        { id: 'HCM-EDGE', label: 'WAN Edge', detail: 'PE pair · LAG to HN' }
      ]
    },
    DC1: {
      compute: [
        { id: 'DC1-POD-1', label: 'Compute Pod 1', detail: 'racks R1-R8 · 64 hypervisors' },
        { id: 'DC1-POD-2', label: 'Compute Pod 2', detail: 'racks R9-R14 · 48 hypervisors' }
      ],
      network: [
        { id: 'DC1-FAB', label: 'Spine-Leaf Fabric', detail: '4 spine · 16 leaf' }
      ],
      storage: [
        { id: 'DC1-CEPH-SSD', label: 'Ceph SSD Tier', detail: '60 OSD · NVMe' },
        { id: 'DC1-CEPH-HDD', label: 'Ceph HDD Tier', detail: '120 OSD · capacity' }
      ],
      control: [
        { id: 'DC1-CTRL', label: 'Control Trio', detail: 'ctrl-dc1-01..03 · Galera' }
      ],
      edge: [
        { id: 'DC1-EDGE', label: 'WAN Edge', detail: 'PE pair · dual uplink' }
      ]
    },
    DC2: {
      compute: [
        { id: 'DC2-POD-1', label: 'Compute Pod 1', detail: 'racks S1-S6 · 40 hypervisors' }
      ],
      network: [
        { id: 'DC2-FAB', label: 'Spine-Leaf Fabric', detail: '2 spine · 8 leaf' }
      ],
      storage: [
        { id: 'DC2-CEPH', label: 'Ceph Mixed Pool', detail: '90 OSD · SSD+HDD' }
      ],
      control: [
        { id: 'DC2-CTRL', label: 'Control Trio', detail: 'ctrl-dc2-01..03 · HA' }
      ],
      edge: [
        { id: 'DC2-EDGE', label: 'WAN Edge', detail: 'PE · single uplink' }
      ]
    }
  };

  /* FNV-1a — a stable, cheap hash so a ticket keeps the same location. */
  function hash(s) {
    let h = 2166136261 >>> 0;
    const str = String(s == null ? '' : s);
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return h >>> 0;
  }

  function caseMs(c) {
    const d = new Date(String(c.opened_at || '').replace(' ', 'T'));
    return isNaN(d.getTime()) ? 0 : d.getTime();
  }

  const pad2 = n => String(n).padStart(2, '0');
  const dayKey = ms => {
    const d = new Date(ms);
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  };

  /* ---------------------------------------------------------------------
   * Topology
   * ------------------------------------------------------------------- */
  const Topology = {
    INFRA_CLASS, CLASS_META, SITE_META, SITE_INFRA,

    classFor(component) {
      return INFRA_CLASS[component] || 'control';
    },

    /** Resolve one case to { site, cls, loc }. Never returns null. */
    locate(c) {
      const site = c.site || 'UNKNOWN';
      const cls = Topology.classFor(c.service_component);
      const list = (SITE_INFRA[site] || {})[cls];
      if (!list || !list.length) {
        return {
          site: site, cls: cls,
          loc: { id: site + '-' + cls.toUpperCase(), label: CLASS_META[cls].label, detail: 'unmapped location' }
        };
      }
      return { site: site, cls: cls, loc: list[hash(c.ticket_id) % list.length] };
    },

    /**
     * Build the Customer > Site > Infra location > Issue tree.
     *
     * opts.openOnly  (default true)  only tickets that are not Closed
     * opts.extra     array of case-shaped objects to merge in — used to put
     *                the ticket currently being typed onto the map before it
     *                has ever been saved.
     */
    tree(customerId, opts) {
      const o = opts || {};
      const cust = global.Customers.byId(customerId);
      if (!cust) return null;

      let cases = global.Cases.byCustomer(customerId).slice();
      if (o.openOnly !== false) cases = cases.filter(c => c.status !== 'Closed');
      if (o.extra && o.extra.length) {
        const have = new Set(cases.map(c => c.ticket_id));
        o.extra.forEach(x => { if (x && !have.has(x.ticket_id)) cases.push(x); });
      }
      cases.sort((a, b) =>
        String(a.priority).localeCompare(String(b.priority)) ||
        caseMs(b) - caseMs(a));

      const siteMap = new Map();
      cases.forEach(c => {
        const at = Topology.locate(c);
        if (!siteMap.has(at.site)) {
          siteMap.set(at.site, { site: at.site, meta: SITE_META[at.site] || { label: at.site, detail: '' }, locs: new Map(), count: 0, p1: 0 });
        }
        const s = siteMap.get(at.site);
        s.count++;
        if (String(c.priority) === '1') s.p1++;
        if (!s.locs.has(at.loc.id)) {
          s.locs.set(at.loc.id, { loc: at.loc, cls: at.cls, meta: CLASS_META[at.cls], issues: [], p1: 0 });
        }
        const l = s.locs.get(at.loc.id);
        l.issues.push(c);
        if (String(c.priority) === '1') l.p1++;
      });

      const sites = Array.from(siteMap.values())
        .map(s => ({
          site: s.site, meta: s.meta, count: s.count, p1: s.p1,
          locations: Array.from(s.locs.values()).sort((a, b) => b.issues.length - a.issues.length)
        }))
        .sort((a, b) => b.count - a.count);

      return {
        customer: cust,
        cases: cases,
        total: cases.length,
        p1: cases.filter(c => String(c.priority) === '1').length,
        site_count: sites.length,
        location_count: sites.reduce((n, s) => n + s.locations.length, 0),
        sites: sites,
        open_only: o.openOnly !== false
      };
    },

    /* --- mermaid emitters -------------------------------------------- */

    /**
     * Mindmap text is indentation-sensitive and has no quoting, so any
     * bracket or quote in a ticket title would break the parse. Strip hard
     * rather than escape — a mind map node is a label, not a payload.
     */
    mmText(s, max) {
      let t = String(s == null ? '' : s)
        .replace(/[\r\n]+/g, ' ')
        .replace(/[()[\]{}<>"'`;|#:]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
      if (max && t.length > max) t = t.slice(0, max - 1).trim() + '…';
      return t || 'untitled';
    },

    /** Flowchart labels ARE quoted, so only " and # actually need removing. */
    fcText(s, max) {
      let t = String(s == null ? '' : s)
        .replace(/[\r\n]+/g, ' ')
        .replace(/["#`]/g, '')
        .replace(/\s+/g, ' ')
        .trim();
      if (max && t.length > max) t = t.slice(0, max - 1).trim() + '…';
      return t || 'untitled';
    },

    /** mermaid `mindmap` source for the tree. */
    mindmap(tree) {
      if (!tree) return 'mindmap\n  root((no customer selected))';
      const T = Topology.mmText;
      const L = [];
      L.push('mindmap');
      L.push('  root((' + T(tree.customer.name, 34) + '))');
      if (!tree.sites.length) {
        L.push('    ' + T('No ' + (tree.open_only ? 'open' : '') + ' tickets - nothing on the map', 60));
        return L.join('\n');
      }
      // Exactly four levels — customer, site, infra location, issue. The infra
      // class and rack detail are folded into the location label rather than
      // hung off it as a child, or they read as a fifth node that is not an
      // issue and the map stops being scannable.
      tree.sites.forEach((s, si) => {
        L.push('    s' + si + '[' + T(s.meta.label + ' — ' + s.count + ' open', 46) + ']');
        s.locations.forEach((l, li) => {
          L.push('      s' + si + 'l' + li + '(' +
            T(l.loc.id + ' · ' + l.loc.label + ' · ' + l.meta.label, 52) + ')');
          l.issues.forEach(c => {
            L.push('        ' + T('S' + c.priority + ' ' + c.ticket_id + ' — ' + c.title, 74));
          });
        });
      });
      return L.join('\n');
    },

    /**
     * mermaid `flowchart` source — same hierarchy, but coloured by severity
     * and clickable, which the mindmap grammar cannot express.
     */
    flowchart(tree, dir) {
      const F = Topology.fcText;
      const L = [];
      L.push('flowchart ' + (dir || 'LR'));
      L.push('  classDef cust fill:#4c1d95,stroke:#2e1065,color:#ffffff,font-weight:bold');
      L.push('  classDef site fill:#1e40af,stroke:#1e3a8a,color:#ffffff');
      L.push('  classDef infra fill:#0f766e,stroke:#115e59,color:#ffffff');
      L.push('  classDef s1 fill:#fde2e2,stroke:#dc3545,stroke-width:2px,color:#7f1d1d');
      L.push('  classDef s2 fill:#ffedd5,stroke:#fd7e14,color:#7c2d12');
      L.push('  classDef s3 fill:#dbeafe,stroke:#0d6efd,color:#1e3a8a');
      L.push('  classDef s4 fill:#e9ecef,stroke:#6c757d,color:#343a40');
      L.push('  classDef here fill:#fff3cd,stroke:#b8860b,stroke-width:3px,color:#664d03');

      if (!tree) { L.push('  C0(["no customer selected"]):::cust'); return L.join('\n'); }

      L.push('  C0(["' + F(tree.customer.name, 40) + '<br/>' + F(tree.customer.tier) +
        ' · ' + tree.total + ' open"]):::cust');

      if (!tree.sites.length) {
        L.push('  N0["No ' + (tree.open_only ? 'open ' : '') + 'tickets"]:::s4');
        L.push('  C0 --> N0');
        return L.join('\n');
      }

      const clicks = [];
      tree.sites.forEach((s, si) => {
        const sid = 'S' + si;
        L.push('  ' + sid + '["' + F(s.meta.label, 32) + '<br/>' + s.count + ' open' +
          (s.p1 ? ' · ' + s.p1 + ' P1' : '') + '"]:::site');
        L.push('  C0 --> ' + sid);
        s.locations.forEach((l, li) => {
          const lid = sid + 'L' + li;
          L.push('  ' + lid + '["' + F(l.loc.id, 24) + '<br/>' + F(l.loc.label, 28) +
            '<br/><small>' + F(l.loc.detail, 34) + '</small>"]:::infra');
          L.push('  ' + sid + ' --> ' + lid);
          l.issues.forEach((c, ii) => {
            const tid = lid + 'T' + ii;
            const cls = c._current ? 'here' : ('s' + (['1', '2', '3', '4'].indexOf(String(c.priority)) >= 0 ? c.priority : '4'));
            L.push('  ' + tid + '["' + F(c.ticket_id, 20) + (c._current ? ' (this ticket)' : '') +
              '<br/>' + F(c.title, 46) + '<br/><small>' + F(c.service_component, 20) +
              ' · S' + F(c.priority) + '</small>"]:::' + cls);
            L.push('  ' + lid + ' --> ' + tid);
            if (!c._current) {
              clicks.push('  click ' + tid + ' call ktTopoOpenCase("' +
                String(c.ticket_id).replace(/[^A-Za-z0-9._-]/g, '') + '")');
            }
          });
        });
      });
      return L.concat(clicks).join('\n');
    }
  };

  /* ---------------------------------------------------------------------
   * History — windowed case query for one customer.
   * ------------------------------------------------------------------- */
  const HISTORY_RANGES = [
    { key: '3d', label: 'Last 3 days', days: 3 },
    { key: '7d', label: 'Last week', days: 7 },
    { key: '30d', label: 'Last month', days: 30 },
    { key: 'custom', label: 'Custom range', days: null }
  ];

  const History = {
    RANGES: HISTORY_RANGES,

    /** Local-midnight-aligned window for a preset key. */
    windowFor(key) {
      const r = HISTORY_RANGES.find(x => x.key === key) || HISTORY_RANGES[1];
      const to = new Date();
      const from = new Date(to.getFullYear(), to.getMonth(), to.getDate());
      from.setDate(from.getDate() - (r.days - 1));
      return { from: from.getTime(), to: to.getTime(), label: r.label, key: r.key, days: r.days };
    },

    /** Inclusive window from two yyyy-mm-dd strings. */
    customWindow(fromStr, toStr) {
      const f = new Date(fromStr + 'T00:00:00');
      const t = new Date(toStr + 'T23:59:59');
      if (isNaN(f.getTime()) || isNaN(t.getTime()) || f > t) return null;
      // Count days midnight-to-midnight. Measuring from the 23:59:59 end
      // instead rounds up to an extra day, and a DST shift inside the range
      // would make which way it rounds depend on the calendar.
      const midnightTo = new Date(toStr + 'T00:00:00');
      return {
        from: f.getTime(), to: t.getTime(), key: 'custom',
        label: fromStr + ' → ' + toStr,
        days: Math.max(1, Math.round((midnightTo - f) / 86400000) + 1)
      };
    },

    /**
     * Every case this customer opened inside the window, plus the rollups the
     * history modal shows. `opened` is the anchor — a ticket belongs to the
     * day it arrived, not the day it closed.
     */
    query(customerId, win) {
      const all = global.Cases.byCustomer(customerId);
      const cases = all
        .filter(c => { const t = caseMs(c); return t >= win.from && t <= win.to; })
        .sort((a, b) => caseMs(b) - caseMs(a));

      const closed = cases.filter(c => c.time_to_resolve_mins != null);
      const open = cases.filter(c => c.time_to_resolve_mins == null);
      const mttr = closed.length
        ? Math.round(closed.reduce((s, c) => s + c.time_to_resolve_mins, 0) / closed.length) : null;

      const tally = field => {
        const m = {};
        cases.forEach(c => { const v = c[field]; if (v) m[v] = (m[v] || 0) + 1; });
        return Object.entries(m).map(([k, n]) => ({ key: k, count: n }))
          .sort((a, b) => b.count - a.count);
      };

      // One bucket per calendar day in the window, so empty days stay visible.
      const days = [];
      const start = new Date(win.from);
      start.setHours(0, 0, 0, 0);
      const span = Math.min(120, Math.max(1, Math.ceil((win.to - start.getTime()) / 86400000)));
      for (let i = 0; i < span; i++) {
        const d = new Date(start.getTime() + i * 86400000);
        days.push({ key: dayKey(d.getTime()), label: pad2(d.getDate()) + '/' + pad2(d.getMonth() + 1), opened: 0, p1: 0 });
      }
      const byKey = new Map(days.map(d => [d.key, d]));
      cases.forEach(c => {
        const b = byKey.get(dayKey(caseMs(c)));
        if (b) { b.opened++; if (String(c.priority) === '1') b.p1++; }
      });

      return {
        customer: global.Customers.byId(customerId),
        window: win,
        cases: cases,
        summary: {
          total: cases.length,
          open: open.length,
          closed: closed.length,
          p1: cases.filter(c => String(c.priority) === '1').length,
          mttr: mttr,
          recurring: tally('problem_id').filter(x => x.count > 1).length,
          components: tally('service_component'),
          sites: tally('site'),
          root_causes: tally('root_cause_category')
        },
        days: days
      };
    },

    /** Flat CSV of the windowed result — what people paste into a review deck. */
    csv(result) {
      const head = ['ticket_id', 'opened_at', 'status', 'priority', 'site', 'infra_location',
        'service_component', 'ttr_mins', 'root_cause_category', 'problem_id', 'kb_id', 'title'];
      const q = v => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
      const rows = result.cases.map(c => {
        const at = Topology.locate(c);
        return [c.ticket_id, c.opened_at, c.status, 'S' + c.priority, c.site, at.loc.id,
          c.service_component, c.time_to_resolve_mins == null ? '' : c.time_to_resolve_mins,
          c.root_cause_category || '', c.problem_id || '', c.kb_id || '', c.title].map(q).join(',');
      });
      return [head.join(','), ...rows].join('\n');
    }
  };

  global.Topology = Topology;
  global.History = History;
  global.KT_TOPOLOGY = { Topology, History, SITE_INFRA, INFRA_CLASS, CLASS_META };
})(window);
