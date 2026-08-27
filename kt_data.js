/* =============================================================================
 * kt_data.js — Customer / Case / Problem entities (ITIL spine)
 *
 * Load AFTER kb_database.js. Extends KB_VOCAB and adds three stores:
 *
 *   Incident (Case)  ->  Problem (root-cause case)  ->  KB Article
 *                    \-> Customer
 *
 * A Problem is the record that OUTLIVES the ticket that found it. It owns the
 * root cause; incidents merely reference it. That is what makes "has this
 * happened before", "is this a recurrence" and "what actually breaks us"
 * answerable at all.
 *
 * Exposes: KT_VOCAB additions, CUSTOMERS, CASE_STORE, PROBLEM_STORE,
 *          Customers, Cases, Problems
 * ========================================================================== */
(function (global) {
  'use strict';

  const CASE_KEY = 'kt_case_store_v1';
  const PROBLEM_KEY = 'kt_problem_store_v1';

  /* ---------------------------------------------------------------------
   * Root-cause taxonomy. Free-text root causes cannot be counted, so
   * "what breaks us most often" is unanswerable without this.
   * 'works-as-designed' is a first-class outcome, not a failure to diagnose.
   * ------------------------------------------------------------------- */
  const ROOT_CAUSE_CATEGORY = [
    { key: 'config-drift', label: 'Configuration drift / bad change' },
    { key: 'capacity', label: 'Capacity / resource exhaustion' },
    { key: 'hardware-fault', label: 'Hardware / optics fault' },
    { key: 'software-defect', label: 'Software defect / bug' },
    { key: 'human-error', label: 'Human error / procedure gap' },
    { key: 'third-party', label: 'Third-party / vendor dependency' },
    { key: 'design-limitation', label: 'Design limitation / architecture' },
    { key: 'data-corruption', label: 'Data / state corruption' },
    { key: 'works-as-designed', label: 'Works as designed (no defect)' },
    { key: 'unknown', label: 'Unknown / not determined' }
  ];

  const PROBLEM_STATUS = [
    { key: 'investigating', label: 'Investigating (root cause unknown)' },
    { key: 'known-error', label: 'Known Error (workaround, no permanent fix)' },
    { key: 'fix-pending', label: 'Fix pending (change scheduled)' },
    { key: 'resolved', label: 'Resolved (permanent fix deployed)' },
    { key: 'works-as-designed', label: 'Works as designed (closed, no fix)' }
  ];

  /* ---------------------------------------------------------------------
   * Customers — controlled entity, replaces the free-text `company` field.
   * ------------------------------------------------------------------- */
  const CUSTOMERS = [
    { customer_id: 'hn-bank', code: 'HNB', name: 'HN-Bank Core Cloud', tier: 'Gold',
      sla_response_mins: 15, sla_resolve_mins: 240,
      sites: ['HN', 'HCMC'], environments: ['production', 'staging'],
      components: ['neutron-ovn', 'nova-metadata', 'rabbitmq', 'nova-compute'],
      contacts: [{ name: 'Linh Tran', role: 'Platform Lead', channel: 'Teams' },
                 { name: 'Minh Vu', role: 'NOC Duty', channel: 'Phone' }] },
    { customer_id: 'hcmc-commerce', code: 'HCC', name: 'HCMC-Commerce Cloud', tier: 'Silver',
      sla_response_mins: 30, sla_resolve_mins: 480,
      sites: ['HCMC'], environments: ['production', 'staging'],
      components: ['keystone', 'horizon', 'neutron-dhcp'],
      contacts: [{ name: 'Anh Nguyen', role: 'Cloud Admin', channel: 'Email' }] },
    { customer_id: 'dc1-shared', code: 'DC1S', name: 'DC1-Shared Cloud', tier: 'Gold',
      sla_response_mins: 15, sla_resolve_mins: 240,
      sites: ['DC1'], environments: ['production'],
      components: ['ceph', 'cinder', 'nova-compute', 'keystone'],
      contacts: [{ name: 'Hoa Pham', role: 'Storage Lead', channel: 'Slack' }] },
    { customer_id: 'dc2-shared', code: 'DC2S', name: 'DC2-Shared Cloud', tier: 'Bronze',
      sla_response_mins: 60, sla_resolve_mins: 960,
      sites: ['DC2'], environments: ['production'],
      components: ['cinder', 'ceph', 'controller-os'],
      contacts: [{ name: 'Tuan Le', role: 'Ops Engineer', channel: 'Email' }] },
    { customer_id: 'multisite', code: 'MSC', name: 'Multi-site Cloud', tier: 'Platinum',
      sla_response_mins: 10, sla_resolve_mins: 120,
      sites: ['HN', 'HCMC', 'DC1', 'DC2'], environments: ['production'],
      components: ['wan-network', 'mysql-galera', 'neutron-ovn'],
      contacts: [{ name: 'Duc Hoang', role: 'Service Delivery Mgr', channel: 'Teams' }] }
  ];

  /* ---------------------------------------------------------------------
   * Problems — the root-cause cases.
   * ------------------------------------------------------------------- */
  const PROBLEM_SEED = [
    { problem_id: 'PRB-0001', status: 'known-error',
      title: 'OVN metadata port not re-created after ovn-metadata-agent restart',
      root_cause_category: 'software-defect',
      root_cause_statement: 'ovn-metadata-agent does not re-provision the distributed metadata port after an unclean restart, so 169.254.169.254 has no backing port on that node. Guests that rely on the network metadata path (config-drive disabled) fail to configure.',
      causal_chain: [
        { why: 'Windows guests fail to configure at first boot', evidence: 'cloudbase-init timeout in guest console log' },
        { why: 'Metadata service is unreachable at 169.254.169.254', evidence: 'nova-api-metadata: "No port found in network None"' },
        { why: 'The distributed metadata port is absent on the compute node', evidence: 'openstack port list --device-owner network:distributed returns nothing for that host' },
        { why: 'ovn-metadata-agent did not re-provision it after restart', evidence: 'agent restarted 14:20; port absent from 14:21 onward' },
        { why: 'Agent has no reconciliation loop on startup (upstream defect)', evidence: 'reproduced in lab twice; upstream bug filed' }
      ],
      workaround: 'Restart neutron-ovn-metadata-agent, or set force_config_drive=True for affected images.',
      permanent_fix: 'Upstream patch pending. Interim: systemd ExecStartPost reconciliation hook on all compute nodes.',
      kb_id: 'KB-2025-0001',
      owner: 'Bob Builder', target_fix_date: '2026-09-30',
      first_seen: '2025-11-14', last_seen: '2026-07-22', recurrence_count: 3 },

    { problem_id: 'PRB-0002', status: 'resolved',
      title: 'ovn-northd saturation under bulk security-group writes',
      root_cause_category: 'design-limitation',
      root_cause_statement: 'Each individual SG rule write triggers a full logical-flow recompute. Bulk pushes amplify Southbound DB writes until northd saturates CPU and the Raft cluster destabilises.',
      causal_chain: [
        { why: 'Tenants see intermittent packet loss', evidence: 'ping loss 30-90s windows' },
        { why: 'Logical flows are computed late', evidence: 'port binding timeouts > 300s' },
        { why: 'ovn-northd is CPU-saturated', evidence: 'northd 100% CPU sustained' },
        { why: 'Bulk SG rule push forces full recompute per rule', evidence: '4000 rules pushed in a loop at 14:15' }
      ],
      workaround: 'Batch SG writes to 50 with a 2s pause.',
      permanent_fix: 'use_logical_dp_groups=true + Raft election timer 16000ms; northd CPU alert at 70%/5min.',
      kb_id: 'KB-2025-0002',
      owner: 'Grace Git', target_fix_date: '2025-11-20',
      first_seen: '2025-11-02', last_seen: '2026-03-11', recurrence_count: 2 },

    { problem_id: 'PRB-0003', status: 'resolved',
      title: 'No snapshot lifecycle policy allows Ceph pools to reach near-full',
      root_cause_category: 'capacity',
      root_cause_statement: 'Snapshots from retired backup jobs were never reclaimed. Combined with organic growth the SSD pool crossed the 0.85 near-full ratio, which throttles client writes and starves provisioning.',
      causal_chain: [
        { why: 'Volume provisioning is slow and intermittently fails', evidence: 'ENOSPC on create; attach latency up' },
        { why: 'Ceph throttles client writes', evidence: 'HEALTH_WARN POOL_NEAR_FULL at 92%' },
        { why: '18TB held by orphaned snapshots', evidence: 'rbd snap ls --all across the pool' },
        { why: 'No lifecycle policy on the retired backup job', evidence: 'job decommissioned 2025-08, snapshots retained' }
      ],
      workaround: 'Route new volumes to the HDD pool below 75% utilisation.',
      permanent_fix: 'Snapshot lifecycle policy + capacity alerts at 70%/80%.',
      kb_id: 'KB-2025-0005',
      owner: 'Heidi Hash', target_fix_date: '2025-10-15',
      first_seen: '2025-09-28', last_seen: '2026-01-08', recurrence_count: 2 },

    { problem_id: 'PRB-0004', status: 'resolved',
      title: 'Keystone key repository drifts across controllers after provider change',
      root_cause_category: 'human-error',
      root_cause_statement: 'The token-provider migration runbook did not include key-repository synchronisation. Tokens validated on a controller lacking the issuing key were treated as revoked.',
      causal_chain: [
        { why: 'Automation gets 401 ~15 min after issuing a token', evidence: 'pipeline logs, 401 Unauthorized' },
        { why: 'Tokens are treated as revoked before TTL', evidence: 'keystone revocation events' },
        { why: 'Validating controller lacks the issuing JWS key', evidence: 'key IDs differ across the three controllers' },
        { why: 'Runbook omitted key-repo sync', evidence: 'change record CHG0004088' }
      ],
      workaround: 'Pin automation to one controller via the load balancer.',
      permanent_fix: 'Key-repo sync step added to the runbook + config-drift check in the deploy pipeline.',
      kb_id: 'KB-2025-0004',
      owner: 'Alice Verify', target_fix_date: '2025-10-05',
      first_seen: '2025-10-09', last_seen: '2025-10-09', recurrence_count: 1 },

    { problem_id: 'PRB-0005', status: 'works-as-designed',
      title: 'Security-group rule propagation delay reported as an outage',
      root_cause_category: 'works-as-designed',
      root_cause_statement: 'OVN propagates security-group changes asynchronously. Convergence of up to 60s at the documented scale is expected behaviour, not a defect. Customers testing connectivity immediately after an API 200 response perceive it as a failure.',
      causal_chain: [
        { why: 'Customer reports SG rule "not working"', evidence: 'connectivity test run 5s after API call' },
        { why: 'Flow has not yet propagated to the hypervisor', evidence: 'ovn-controller applies flow at T+38s' },
        { why: 'Propagation is asynchronous by design', evidence: 'platform SLA doc §4.2: up to 60s convergence' }
      ],
      workaround: 'Wait 60s before validating, or poll until the flow appears.',
      permanent_fix: 'None required. Documentation link sent to customer; API response note added.',
      kb_id: 'KB-2025-0013',
      owner: 'Charlie Checker', target_fix_date: null,
      first_seen: '2026-02-03', last_seen: '2026-08-02', recurrence_count: 4 },

    { problem_id: 'PRB-0006', status: 'resolved',
      title: 'Degrading WAN LAG optic causes partial inter-site packet loss',
      root_cause_category: 'hardware-fault',
      root_cause_statement: 'One LAG member transceiver degraded below Rx power threshold, producing CRC errors while the LAG kept hashing a share of flows onto it.',
      causal_chain: [
        { why: 'Cross-site replication lags and API calls fail', evidence: 'galera receive queue growing' },
        { why: '8-10% packet loss on the inter-site path', evidence: 'monitoring, TCP retransmits' },
        { why: 'One LAG member drops frames', evidence: 'CRC error counter on member 3' },
        { why: 'Transceiver Rx power below threshold', evidence: 'show interface transceiver detail' }
      ],
      workaround: 'Remove the degraded member from the LAG; run at reduced bandwidth.',
      permanent_fix: 'Transceiver replaced; per-member Rx-power and CRC alerting added.',
      kb_id: 'KB-2025-0010',
      owner: 'Frank Fixer', target_fix_date: '2025-08-05',
      first_seen: '2025-08-01', last_seen: '2025-08-01', recurrence_count: 1 }
  ];

  /* ---------------------------------------------------------------------
   * Historical cases.
   * Compact tuple form keeps this readable; expandCase() inflates it.
   * [ id, customer, opened, ttr_mins, component, category, env, site,
   *   priority, rc_category, problem_id, kb_id, title, signature ]
   * ------------------------------------------------------------------- */
  const CASE_SEED = [
    ['INC0008812', 'hn-bank', '2025-11-14 09:05', 215, 'nova-metadata', 'networking', 'production', 'HN', '1', 'software-defect', 'PRB-0001', 'KB-2025-0001', 'Windows VM create fails - No port found for metadata IP', 'No port found in network None with IP address 169.254.169.254'],
    ['INC0009142', 'hn-bank', '2026-03-04 11:20', 42, 'nova-metadata', 'networking', 'production', 'HN', '2', 'software-defect', 'PRB-0001', 'KB-2025-0001', 'Windows guests unconfigured after agent restart', 'No port found in network None with IP address 169.254.169.254'],
    ['INC0009477', 'hn-bank', '2026-07-22 08:12', 28, 'nova-metadata', 'networking', 'production', 'HN', '2', 'software-defect', 'PRB-0001', 'KB-2025-0001', 'Metadata unreachable on compute-hn-04', 'No port found in network None with IP address 169.254.169.254'],

    ['INC0008790', 'hn-bank', '2025-11-02 14:15', 340, 'neutron-ovn', 'networking', 'production', 'HN', '1', 'design-limitation', 'PRB-0002', 'KB-2025-0002', 'Packet loss and port binding delays after bulk SG push', 'ovn-northd Raft leader election; port binding timed out after 300 seconds'],
    ['INC0009205', 'hn-bank', '2026-03-11 16:40', 95, 'neutron-ovn', 'networking', 'production', 'HN', '2', 'design-limitation', 'PRB-0002', 'KB-2025-0002', 'northd CPU spike during policy rollout', 'ovn-northd Raft leader election; port binding timed out'],

    ['INC0008498', 'hn-bank', '2025-09-05 22:15', 300, 'rabbitmq', 'messaging', 'production', 'HN', '1', 'third-party', null, 'KB-2025-0007', 'RabbitMQ partition during switch firmware upgrade', 'MessagingTimeout: Timed out waiting for a reply to message ID'],
    ['INC0009320', 'hn-bank', '2026-05-18 10:00', 65, 'nova-compute', 'compute', 'production', 'HN', '3', 'config-drift', null, null, 'Scheduler skipping two compute nodes', 'nova-compute State: down Updated_At older than service_down_time'],
    ['INC0009501', 'hn-bank', '2026-08-02 09:30', 25, 'neutron-ovn', 'networking', 'production', 'HN', '3', 'works-as-designed', 'PRB-0005', 'KB-2025-0013', 'New SG rule reported as not applying', 'security group rule added but traffic still blocked'],

    ['INC0008460', 'hcmc-commerce', '2025-08-27 10:05', 165, 'horizon', 'identity', 'staging', 'HCMC', '3', 'config-drift', null, 'KB-2025-0008', 'Horizon 500 after IdP redirect for new users', 'Internal Server Error /auth/websso/'],
    ['INC0008701', 'hcmc-commerce', '2025-10-21 16:40', 260, 'neutron-dhcp', 'networking', 'production', 'HCMC', '2', 'config-drift', null, 'KB-2025-0003', 'Intermittent DHCP lease failures after MTU change', 'DHCPDISCOVER no address available lease allocation failed'],
    ['INC0009088', 'hcmc-commerce', '2026-02-03 13:25', 30, 'neutron-ovn', 'networking', 'production', 'HCMC', '3', 'works-as-designed', 'PRB-0005', 'KB-2025-0013', 'SG change appears to have no effect', 'security group rule added but traffic still blocked'],
    ['INC0009260', 'hcmc-commerce', '2026-04-09 09:50', 210, 'keystone', 'identity', 'production', 'HCMC', '2', 'capacity', null, null, 'API 504 timeouts at peak hours', '504 Gateway Timeout from nova/neutron API'],
    ['INC0009412', 'hcmc-commerce', '2026-06-27 15:05', 22, 'neutron-ovn', 'networking', 'production', 'HCMC', '4', 'works-as-designed', 'PRB-0005', 'KB-2025-0013', 'Customer says firewall rule not active', 'security group rule added but traffic still blocked'],

    ['INC0008590', 'dc1-shared', '2025-09-28 16:00', 480, 'ceph', 'storage', 'production', 'DC1', '1', 'capacity', 'PRB-0003', 'KB-2025-0005', 'Ceph SSD pool near-full, volume creation failing', 'HEALTH_WARN pool nearfull POOL_NEAR_FULL ENOSPC creating volume'],
    ['INC0009012', 'dc1-shared', '2026-01-08 08:40', 190, 'ceph', 'storage', 'production', 'DC1', '2', 'capacity', 'PRB-0003', 'KB-2025-0005', 'Pool utilisation back above 85%', 'HEALTH_WARN pool nearfull POOL_NEAR_FULL'],
    ['INC0008655', 'dc1-shared', '2025-10-09 11:15', 190, 'keystone', 'identity', 'production', 'DC1', '2', 'human-error', 'PRB-0004', 'KB-2025-0004', 'Service account tokens revoked before TTL', '401 Unauthorized token revoked'],
    ['INC0008401', 'dc1-shared', '2025-08-12 07:30', 55, 'nova-compute', 'compute', 'production', 'DC1', '2', 'config-drift', null, 'KB-2025-0009', 'nova-compute down on compute-dc1-03 (clock skew)', 'nova-compute State: down Updated_At older than service_down_time'],
    ['INC0009355', 'dc1-shared', '2026-05-30 14:10', 48, 'nova-compute', 'compute', 'production', 'DC1', '3', 'config-drift', null, 'KB-2025-0009', 'Compute node unschedulable again after patch', 'nova-compute State: down Updated_At older than service_down_time'],
    ['INC0009489', 'dc1-shared', '2026-07-29 10:25', null, 'cinder', 'storage', 'production', 'DC1', '2', null, null, null, 'Volume attach latency creeping up', 'volume remained in attaching state rbd map timed out'],

    ['INC0008544', 'dc2-shared', '2025-09-19 17:30', 150, 'cinder', 'storage', 'production', 'DC2', '2', 'capacity', null, 'KB-2025-0006', 'Slow volume attach during OSD backfill', 'volume remained in attaching state rbd map timed out'],
    ['INC0008300', 'dc2-shared', '2025-07-04 23:00', 90, 'controller-os', 'availability', 'production', 'DC2', '3', 'human-error', null, 'KB-2025-0012', 'API 500 bursts during rolling maintenance', 'HTTP 500 Internal Server Error haproxy server api/ctrl2 is DOWN'],
    ['INC0009166', 'dc2-shared', '2026-02-22 21:15', 120, 'controller-os', 'availability', 'production', 'DC2', '3', 'human-error', null, 'KB-2025-0012', 'Errors again during patch window', 'HTTP 500 Internal Server Error haproxy server is DOWN'],
    ['INC0009398', 'dc2-shared', '2026-06-14 19:45', 175, 'cinder', 'storage', 'production', 'DC2', '2', 'capacity', null, 'KB-2025-0006', 'Attach timeouts after OSD replacement', 'volume remained in attaching state rbd map timed out'],
    ['INC0009510', 'dc2-shared', '2026-08-11 08:00', null, 'ceph', 'storage', 'production', 'DC2', '3', null, null, null, 'Recovery traffic impacting client IO', 'slow ops osd recovery backfill'],

    ['INC0008377', 'multisite', '2025-08-01 12:10', 420, 'wan-network', 'networking', 'production', 'HN', '1', 'hardware-fault', 'PRB-0006', 'KB-2025-0010', 'Inter-site WAN packet loss degrading replication', 'ping statistics 10% packet loss TCP retransmits elevated'],
    ['INC0009230', 'multisite', '2026-03-28 06:30', 260, 'mysql-galera', 'availability', 'production', 'DC1', '1', 'third-party', null, null, 'Galera node desync after network blip', 'wsrep_local_recv_queue growing node desynced'],
    ['INC0009444', 'multisite', '2026-07-08 17:20', 310, 'wan-network', 'networking', 'production', 'HCMC', '1', 'hardware-fault', 'PRB-0006', 'KB-2025-0010', 'Latency spikes on secondary WAN path', 'ping statistics packet loss TCP retransmits elevated'],
    ['INC0009515', 'multisite', '2026-08-14 11:40', null, 'neutron-ovn', 'networking', 'production', 'HN', '2', null, null, null, 'Intermittent east-west connectivity reports', 'intermittent packet loss between tenant networks']
  ];

  const CASE_FIELDS = ['ticket_id', 'customer_id', 'opened_at', 'time_to_resolve_mins',
    'service_component', 'category', 'environment', 'site', 'priority',
    'root_cause_category', 'problem_id', 'kb_id', 'title', 'error_signature_raw'];

  function expandCase(tuple) {
    const c = {};
    CASE_FIELDS.forEach((f, i) => { c[f] = tuple[i]; });
    c.status = c.time_to_resolve_mins == null ? 'In Progress' : 'Closed';
    c.closed_at = c.time_to_resolve_mins == null ? null
      : new Date(new Date(c.opened_at.replace(' ', 'T') + ':00+07:00').getTime() + c.time_to_resolve_mins * 60000).toISOString();
    c.error_signature_norm = (global.KB && global.KB.normalizeErrorSignature)
      ? global.KB.normalizeErrorSignature(c.error_signature_raw) : c.error_signature_raw;
    c.deviation = c.title;
    c.relations = [];
    c.events = [
      { at: c.opened_at, type: 'created', detail: 'Case opened (P' + c.priority + ')' }
    ];
    if (c.kb_id) c.events.push({ at: c.opened_at, type: 'kb_applied', detail: 'Applied ' + c.kb_id });
    if (c.problem_id) c.events.push({ at: c.opened_at, type: 'problem_linked', detail: 'Linked to ' + c.problem_id });
    if (c.closed_at) c.events.push({ at: c.closed_at.slice(0, 16).replace('T', ' '), type: 'closed', detail: 'Resolved in ' + c.time_to_resolve_mins + ' min' });
    return c;
  }

  /* ---------------------------------------------------------------------
   * Stores + persistence
   * ------------------------------------------------------------------- */
  function loadStore(key, seed) {
    let extra = [];
    try {
      const raw = localStorage.getItem(key);
      if (raw) extra = JSON.parse(raw) || [];
    } catch (e) { /* ignore */ }
    const out = seed.slice();
    extra.forEach(x => {
      const id = x.ticket_id || x.problem_id;
      if (!out.some(y => (y.ticket_id || y.problem_id) === id)) out.push(x);
    });
    return out;
  }

  function saveStore(key, store, localOnlyFlag) {
    try {
      localStorage.setItem(key, JSON.stringify(store.filter(x => x[localOnlyFlag])));
      return true;
    } catch (e) { return false; }
  }

  global.CASE_STORE = loadStore(CASE_KEY, CASE_SEED.map(expandCase));
  global.PROBLEM_STORE = loadStore(PROBLEM_KEY, PROBLEM_SEED);

  /* ---------------------------------------------------------------------
   * Customers API
   * ------------------------------------------------------------------- */
  const Customers = {
    all: () => CUSTOMERS,
    byId: id => CUSTOMERS.find(c => c.customer_id === id) || null,

    /** Rolling health snapshot used by the ticket header and Customer 360. */
    health(customerId, windowDays) {
      const days = windowDays || 90;
      const cutoff = Date.now() - days * 86400000;
      const all = Cases.byCustomer(customerId);
      const recent = all.filter(c => new Date(c.opened_at.replace(' ', 'T')).getTime() >= cutoff);
      const closed = recent.filter(c => c.time_to_resolve_mins != null);
      const mttr = closed.length
        ? Math.round(closed.reduce((s, c) => s + c.time_to_resolve_mins, 0) / closed.length) : null;

      const older = all.filter(c => {
        const t = new Date(c.opened_at.replace(' ', 'T')).getTime();
        return t < cutoff && t >= cutoff - days * 86400000 && c.time_to_resolve_mins != null;
      });
      const prevMttr = older.length
        ? Math.round(older.reduce((s, c) => s + c.time_to_resolve_mins, 0) / older.length) : null;

      const byComponent = {};
      all.forEach(c => { byComponent[c.service_component] = (byComponent[c.service_component] || 0) + 1; });
      const topComponents = Object.entries(byComponent).sort((a, b) => b[1] - a[1]).slice(0, 4);

      const openProblems = Array.from(new Set(all.map(c => c.problem_id).filter(Boolean)))
        .map(id => Problems.byId(id))
        .filter(p => p && !['resolved', 'works-as-designed'].includes(p.status));

      return {
        customer: Customers.byId(customerId),
        total_cases: all.length,
        window_days: days,
        cases_in_window: recent.length,
        open_cases: all.filter(c => c.status !== 'Closed').length,
        mttr_mins: mttr,
        mttr_trend: (mttr != null && prevMttr != null)
          ? (mttr > prevMttr ? 'up' : mttr < prevMttr ? 'down' : 'flat') : 'n/a',
        top_components: topComponents,
        open_problems: openProblems,
        recurring: Cases.recurringFor(customerId)
      };
    }
  };

  /* ---------------------------------------------------------------------
   * Cases API
   * ------------------------------------------------------------------- */
  const Cases = {
    all: () => global.CASE_STORE,
    byId: id => global.CASE_STORE.find(c => c.ticket_id === id) || null,
    byCustomer: id => global.CASE_STORE.filter(c => c.customer_id === id),
    byProblem: id => global.CASE_STORE.filter(c => c.problem_id === id),
    byComponent: comp => global.CASE_STORE.filter(c => c.service_component === comp),

    /**
     * Four relation types, each with the reason it was drawn. This is the
     * "related customer cases" panel.
     */
    related(ctx, limit) {
      const cap = limit || 6;
      const norm = ctx.error_signature_norm ||
        (global.KB ? global.KB.normalizeErrorSignature(ctx.error_signature_raw || '') : '');
      const out = [];
      const seen = new Set([ctx.ticket_id]);

      const push = (c, type, why, conf) => {
        if (seen.has(c.ticket_id)) return;
        seen.add(c.ticket_id);
        out.push({ case: c, relation: type, why, confidence: conf });
      };

      global.CASE_STORE.forEach(c => {
        if (norm && c.error_signature_norm === norm) {
          push(c, 'same-signature', 'identical normalized error signature', 0.95);
        }
      });
      global.CASE_STORE.forEach(c => {
        if (norm && c.error_signature_norm && c.error_signature_norm !== norm &&
            (c.error_signature_norm.includes(norm) || norm.includes(c.error_signature_norm))) {
          push(c, 'same-signature', 'overlapping error signature', 0.7);
        }
      });
      if (ctx.problem_id) {
        Cases.byProblem(ctx.problem_id).forEach(c =>
          push(c, 'same-problem', 'linked to ' + ctx.problem_id, 0.9));
      }
      if (ctx.customer_id && ctx.service_component) {
        global.CASE_STORE.forEach(c => {
          if (c.customer_id === ctx.customer_id && c.service_component === ctx.service_component) {
            push(c, 'same-customer-component', 'same customer + ' + c.service_component, 0.6);
          }
        });
      }
      if (ctx.customer_id) {
        global.CASE_STORE.forEach(c => {
          if (c.customer_id === ctx.customer_id) push(c, 'same-customer', 'same customer history', 0.35);
        });
      }
      return out.sort((a, b) => b.confidence - a.confidence).slice(0, cap);
    },

    /** Signatures this customer has seen more than once — recurrence signal. */
    recurringFor(customerId) {
      const groups = {};
      Cases.byCustomer(customerId).forEach(c => {
        const k = c.problem_id || c.error_signature_norm;
        if (!k) return;
        (groups[k] = groups[k] || []).push(c);
      });
      return Object.entries(groups)
        .filter(([, list]) => list.length > 1)
        .map(([k, list]) => ({
          key: k,
          problem: Problems.byId(k),
          count: list.length,
          cases: list.sort((a, b) => a.opened_at.localeCompare(b.opened_at)),
          last_seen: list.map(c => c.opened_at).sort().pop()
        }))
        .sort((a, b) => b.count - a.count);
    },

    /** Median TTR for a component — the baseline the AI plan is measured against. */
    baselineMinutes(component) {
      const v = global.CASE_STORE
        .filter(c => c.service_component === component && c.time_to_resolve_mins != null)
        .map(c => c.time_to_resolve_mins).sort((a, b) => a - b);
      if (!v.length) return null;
      const m = Math.floor(v.length / 2);
      return v.length % 2 ? v[m] : Math.round((v[m - 1] + v[m]) / 2);
    },

    /**
     * What this issue cost the FIRST time, before a KB article existed.
     * That is the true counterfactual for "what did knowing this save us" —
     * the median is already depressed by the fast repeat fixes the KB enabled.
     */
    firstTimeMinutes(problemId) {
      const v = Cases.byProblem(problemId)
        .filter(c => c.time_to_resolve_mins != null)
        .sort((a, b) => a.opened_at.localeCompare(b.opened_at));
      return v.length ? v[0].time_to_resolve_mins : null;
    },

    /** Pareto of root causes: what actually breaks us. */
    rootCausePareto(customerId) {
      const src = customerId ? Cases.byCustomer(customerId) : global.CASE_STORE;
      const agg = {};
      src.forEach(c => {
        if (!c.root_cause_category) return;
        const a = agg[c.root_cause_category] = agg[c.root_cause_category] || { count: 0, mins: 0 };
        a.count++;
        a.mins += c.time_to_resolve_mins || 0;
      });
      return Object.entries(agg)
        .map(([key, v]) => ({
          key,
          label: (ROOT_CAUSE_CATEGORY.find(r => r.key === key) || {}).label || key,
          count: v.count, total_mins: v.mins
        }))
        .sort((a, b) => b.count - a.count);
    },

    add(c) {
      c._local = true;
      const i = global.CASE_STORE.findIndex(x => x.ticket_id === c.ticket_id);
      if (i >= 0) global.CASE_STORE[i] = c; else global.CASE_STORE.push(c);
      saveStore(CASE_KEY, global.CASE_STORE, '_local');
      return c;
    },

    timeline(id) {
      const c = Cases.byId(id);
      return c ? (c.events || []).slice().sort((a, b) => String(a.at).localeCompare(String(b.at))) : [];
    }
  };

  /* ---------------------------------------------------------------------
   * Problems API
   * ------------------------------------------------------------------- */
  const Problems = {
    all: () => global.PROBLEM_STORE,
    byId: id => global.PROBLEM_STORE.find(p => p.problem_id === id) || null,
    byStatus: s => global.PROBLEM_STORE.filter(p => p.status === s),

    affectedCustomers(problemId) {
      return Array.from(new Set(Cases.byProblem(problemId).map(c => c.customer_id)))
        .map(id => Customers.byId(id)).filter(Boolean);
    },

    /** Match an incoming signature to an existing Problem — the triage lookup. */
    forSignature(norm, component) {
      if (!norm) return null;
      const hits = global.CASE_STORE.filter(c =>
        c.problem_id && c.error_signature_norm &&
        (c.error_signature_norm === norm ||
         c.error_signature_norm.includes(norm) || norm.includes(c.error_signature_norm)) &&
        (!component || c.service_component === component));
      if (!hits.length) return null;
      const counts = {};
      hits.forEach(c => { counts[c.problem_id] = (counts[c.problem_id] || 0) + 1; });
      const best = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
      return Problems.byId(best[0]);
    },

    nextId() {
      const n = global.PROBLEM_STORE
        .map(p => parseInt((/^PRB-(\d+)$/.exec(p.problem_id) || [, '0'])[1], 10));
      return 'PRB-' + String(Math.max(0, ...n) + 1).padStart(4, '0');
    },

    create(p) {
      p.problem_id = p.problem_id || Problems.nextId();
      p._local = true;
      p.first_seen = p.first_seen || new Date().toISOString().slice(0, 10);
      p.last_seen = p.last_seen || p.first_seen;
      p.recurrence_count = p.recurrence_count || 1;
      p.linked_cases = p.linked_cases || [];
      global.PROBLEM_STORE.push(p);
      saveStore(PROBLEM_KEY, global.PROBLEM_STORE, '_local');
      return p;
    },

    linkCase(problemId, ticketId) {
      const p = Problems.byId(problemId);
      const c = Cases.byId(ticketId);
      if (!p) return null;
      p.linked_cases = Array.from(new Set((p.linked_cases || []).concat(ticketId)));
      p.last_seen = new Date().toISOString().slice(0, 10);
      p.recurrence_count = (p.recurrence_count || 0) + 1;
      p._local = true;
      if (c) {
        c.problem_id = problemId;
        c.events = (c.events || []).concat({
          at: new Date().toISOString().slice(0, 16).replace('T', ' '),
          type: 'problem_linked', detail: 'Linked to ' + problemId
        });
        Cases.add(c);
      }
      saveStore(PROBLEM_KEY, global.PROBLEM_STORE, '_local');
      return p;
    },

    /** Unlinked cases sharing a signature — "these look like one Problem". */
    clusterCandidates(minSize) {
      const min = minSize || 2;
      const groups = {};
      global.CASE_STORE.forEach(c => {
        if (c.problem_id || !c.error_signature_norm) return;
        (groups[c.error_signature_norm] = groups[c.error_signature_norm] || []).push(c);
      });
      return Object.entries(groups)
        .filter(([, list]) => list.length >= min)
        .map(([sig, list]) => ({
          signature: sig,
          cases: list,
          customers: Array.from(new Set(list.map(c => c.customer_id))),
          components: Array.from(new Set(list.map(c => c.service_component))),
          total_mins: list.reduce((s, c) => s + (c.time_to_resolve_mins || 0), 0)
        }))
        .sort((a, b) => b.cases.length - a.cases.length);
    },

    stats() {
      return {
        total: global.PROBLEM_STORE.length,
        open: global.PROBLEM_STORE.filter(p => ['investigating', 'known-error', 'fix-pending'].includes(p.status)).length,
        known_errors: Problems.byStatus('known-error').length,
        wad: Problems.byStatus('works-as-designed').length,
        recurrences: global.PROBLEM_STORE.reduce((s, p) => s + Math.max(0, (p.recurrence_count || 1) - 1), 0)
      };
    }
  };

  /* ---------------------------------------------------------------------
   * Publish
   * ------------------------------------------------------------------- */
  if (global.KB_VOCAB) {
    global.KB_VOCAB.root_cause_category = ROOT_CAUSE_CATEGORY;
    global.KB_VOCAB.problem_status = PROBLEM_STATUS;
    global.KB_VOCAB.customer = CUSTOMERS.map(c => ({ key: c.customer_id, label: c.name + ' (' + c.tier + ')' }));
  }

  global.ROOT_CAUSE_CATEGORY = ROOT_CAUSE_CATEGORY;
  global.PROBLEM_STATUS = PROBLEM_STATUS;
  global.CUSTOMERS = CUSTOMERS;
  global.Customers = Customers;
  global.Cases = Cases;
  global.Problems = Problems;
  /**
   * Inflate a raw case object (same shape as CASE_SEED expands to) and push it
   * into the store. Used by demo_tickets.js to seed each demo ticket's
   * "related / similar issue" companion case.
   */
  function ingestCase(raw) {
    const c = Object.assign({}, raw);
    c.status = c.time_to_resolve_mins == null ? 'In Progress' : 'Closed';
    if (!c.closed_at && c.time_to_resolve_mins != null) {
      c.closed_at = new Date(new Date(c.opened_at.replace(' ', 'T') + ':00+07:00').getTime()
        + c.time_to_resolve_mins * 60000).toISOString();
    }
    c.error_signature_norm = (global.KB && global.KB.normalizeErrorSignature)
      ? global.KB.normalizeErrorSignature(c.error_signature_raw) : c.error_signature_raw;
    c.deviation = c.deviation || c.title;
    c.relations = c.relations || [];
    if (!c.events) {
      c.events = [{ at: c.opened_at, type: 'created', detail: 'Case opened (P' + c.priority + ')' }];
      if (c.kb_id) c.events.push({ at: c.opened_at, type: 'kb_applied', detail: 'Applied ' + c.kb_id });
      if (c.problem_id) c.events.push({ at: c.opened_at, type: 'problem_linked', detail: 'Linked to ' + c.problem_id });
      if (c.closed_at) c.events.push({ at: c.closed_at.slice(0, 16).replace('T', ' '), type: 'closed', detail: 'Resolved in ' + c.time_to_resolve_mins + ' min' });
    }
    if (!global.CASE_STORE.some(x => x.ticket_id === c.ticket_id)) global.CASE_STORE.push(c);
    return c;
  }

  /* ---------------------------------------------------------------------
   * Recent activity generator.
   *
   * The seeded history spans ~12 months, so a "last 24 hours" dashboard view
   * would be empty. This synthesises a realistic recent tail RELATIVE TO NOW,
   * so the demo never goes stale no matter when it is shown.
   *
   * Deterministic: seeded by the calendar day, so a demo does not reshuffle
   * itself between reloads.
   * ------------------------------------------------------------------- */
  function makeRng(seed) {
    let s = seed >>> 0;
    return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
  }

  const RECENT_TEMPLATES = [
    ['nova-metadata', 'networking', 'software-defect', 'PRB-0001', 'KB-2025-0001', 'Windows guest unconfigured after metadata timeout', 'No port found in network None with IP address 169.254.169.254'],
    ['neutron-ovn', 'networking', 'design-limitation', 'PRB-0002', 'KB-2025-0002', 'Port binding latency during policy push', 'ovn-northd Raft leader election; port binding timed out'],
    ['neutron-ovn', 'networking', 'works-as-designed', 'PRB-0005', 'KB-2025-0013', 'Security group rule reported as not applying', 'security group rule added but traffic still blocked'],
    ['ceph', 'storage', 'capacity', 'PRB-0003', 'KB-2025-0005', 'Pool utilisation warning on SSD tier', 'HEALTH_WARN pool nearfull POOL_NEAR_FULL'],
    ['cinder', 'storage', 'capacity', null, 'KB-2025-0006', 'Volume attach exceeding timeout', 'volume remained in attaching state rbd map timed out'],
    ['keystone', 'identity', 'human-error', 'PRB-0004', 'KB-2025-0004', 'Service account token rejected early', '401 Unauthorized token revoked'],
    ['rabbitmq', 'messaging', 'third-party', null, 'KB-2025-0007', 'RPC timeout during agent restart', 'MessagingTimeout: Timed out waiting for a reply to message ID'],
    ['nova-compute', 'compute', 'config-drift', null, 'KB-2025-0009', 'Compute service marked down by scheduler', 'nova-compute State: down Updated_At older than service_down_time'],
    ['neutron-dhcp', 'networking', 'config-drift', null, 'KB-2025-0003', 'DHCP lease failures on batch boot', 'DHCPDISCOVER no address available lease allocation failed'],
    ['horizon', 'identity', 'config-drift', null, 'KB-2025-0008', 'Dashboard 500 after IdP redirect', 'Internal Server Error /auth/websso/'],
    ['wan-network', 'networking', 'hardware-fault', 'PRB-0006', 'KB-2025-0010', 'Inter-site latency spike', 'ping statistics packet loss TCP retransmits elevated'],
    ['controller-os', 'availability', 'human-error', null, 'KB-2025-0012', 'API 5xx burst during patching', 'HTTP 500 Internal Server Error haproxy server is DOWN'],
    ['octavia', 'networking', 'unknown', null, null, 'Load balancer listener slow to provision', 'listener stuck in PENDING_UPDATE provisioning_status'],
    ['glance', 'image-guest', 'config-drift', null, null, 'Image upload checksum mismatch', 'image upload failed checksum mismatch'],
    ['mysql-galera', 'availability', 'third-party', null, null, 'Galera node desync under write load', 'wsrep_local_recv_queue growing node desynced'],

    /* Flex AI. The accelerator estate fails differently from the IaaS fabric —
       its cases cluster on memory and scheduling rather than on networking —
       which is exactly why it deserves its own health gauge instead of being
       averaged into Flex. */
    ['gpu-node', 'compute', 'hardware-fault', null, 'KB-2025-0021', 'GPU falls off the bus mid-job', 'NVRM: Xid 79 GPU has fallen off the bus'],
    ['gpu-node', 'compute', 'hardware-fault', null, 'KB-2025-0024', 'Uncorrectable ECC error retires a GPU', 'NVRM: Xid 48 double-bit ECC error; row remap pending, GPU marked unhealthy'],
    ['gpu-node', 'compute', 'capacity', null, 'KB-2025-0025', 'Thermal slowdown caps training clocks', 'nvidia-smi: SW Thermal Slowdown Active; clocks capped at 810 MHz, GPU temp 88C'],
    ['gpu-scheduler', 'compute', 'capacity', 'PRB-0007', 'KB-2025-0022', 'Training pods pending, no allocatable GPU', '0/12 nodes are available: 12 Insufficient nvidia.com/gpu'],
    ['gpu-scheduler', 'compute', 'capacity', 'PRB-0007', null, 'GPU queue saturated under batch submission', 'DCGM_FI_DEV_GPU_UTIL 100% sustained; scheduler queue depth 47, oldest pending 68m'],
    ['gpu-fabric', 'networking', 'hardware-fault', null, 'KB-2025-0026', 'NVLink error stalls collective operations', 'NCCL WARN NET/IB: got completion with error 12; NCCL timeout in ncclAllReduce'],
    ['gpu-driver', 'compute', 'config-drift', null, 'KB-2025-0027', 'Driver/CUDA mismatch after node reboot', 'CUDA driver version is insufficient for CUDA runtime version; nvidia-smi has failed'],
    ['model-serving', 'availability', 'capacity', 'PRB-0007', 'KB-2025-0023', 'Inference OOM under concurrent load', 'CUDA out of memory. Tried to allocate 2.00 GiB'],
    ['inference-gateway', 'availability', 'config-drift', null, null, 'Inference requests time out at the gateway', 'upstream request timeout while awaiting model response'],
    ['vector-store', 'storage', 'capacity', null, null, 'Embedding index rebuild exceeds window', 'index build aborted: memory limit exceeded during HNSW insert']
  ];

  const CUSTOMER_WEIGHTS = [
    ['hn-bank', 30], ['hcmc-commerce', 24], ['dc1-shared', 20], ['dc2-shared', 14], ['multisite', 12]
  ];

  function pickWeighted(rng, pairs) {
    const total = pairs.reduce((s, p) => s + p[1], 0);
    let r = rng() * total;
    for (const [k, w] of pairs) { r -= w; if (r <= 0) return k; }
    return pairs[pairs.length - 1][0];
  }

  function generateRecentCases(count) {
    const now = new Date();
    const daySeed = Math.floor(now.getTime() / 86400000);
    const rng = makeRng(daySeed);
    const out = [];
    const n = count || 64;

    for (let i = 0; i < n; i++) {
      // Weighted toward the last 48h so 1d / 3d views are populated.
      const r = rng();
      const daysBack = r < 0.22 ? rng() * 1 : r < 0.45 ? 1 + rng() * 2 : r < 0.75 ? 3 + rng() * 4 : 7 + rng() * 23;
      const opened = new Date(now.getTime() - daysBack * 86400000);
      const tpl = RECENT_TEMPLATES[Math.floor(rng() * RECENT_TEMPLATES.length)];
      const customer = pickWeighted(rng, CUSTOMER_WEIGHTS);
      const cust = CUSTOMERS.find(c => c.customer_id === customer);
      const site = cust.sites[Math.floor(rng() * cust.sites.length)];

      // Older cases are mostly closed; very recent ones are often still open.
      const closedChance = daysBack < 0.5 ? 0.25 : daysBack < 2 ? 0.6 : 0.9;
      const isClosed = rng() < closedChance;
      const ttr = isClosed ? Math.round(20 + rng() * 400) : null;
      const pri = rng() < 0.12 ? '1' : rng() < 0.4 ? '2' : rng() < 0.8 ? '3' : '4';

      const pad = x => String(x).padStart(2, '0');
      const openedStr = opened.getFullYear() + '-' + pad(opened.getMonth() + 1) + '-' + pad(opened.getDate()) +
        ' ' + pad(opened.getHours()) + ':' + pad(opened.getMinutes());

      out.push({
        ticket_id: 'INC' + String(9600 + i).padStart(7, '0'),
        customer_id: customer,
        opened_at: openedStr,
        time_to_resolve_mins: ttr,
        service_component: tpl[0], category: tpl[1],
        environment: 'production', site: site, priority: pri,
        root_cause_category: isClosed ? tpl[2] : null,
        problem_id: isClosed ? tpl[3] : null,
        kb_id: isClosed ? tpl[4] : null,
        title: tpl[5], error_signature_raw: tpl[6],
        _generated: true
      });
    }
    /* Guarantee a live duplicate for the signatures the demo leads with.
       Without this the "Same issue already open?" answer depends on the
       day's random seed, and the most important thing triage reports would
       intermittently show "No". These are open, recent, and deliberate. */
    const pad = x => String(x).padStart(2, '0');
    const stamp = hoursAgo => {
      const d = new Date(now.getTime() - hoursAgo * 3600000);
      return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
        ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    };
    [
      [0, 'hn-bank', 'HN', '2', 3],    // metadata — same signature as DEMO-001
      [0, 'hcmc-commerce', 'HCMC', '3', 9],
      [3, 'dc1-shared', 'DC1', '2', 6],  // ceph near-full — same as DEMO-004
      [2, 'hcmc-commerce', 'HCMC', '4', 14]  // SG propagation — same as DEMO-003
    ].forEach(([ti, cust, site, pri, hrs], i) => {
      const tpl = RECENT_TEMPLATES[ti];
      out.push({
        ticket_id: 'INC' + String(9700 + i).padStart(7, '0'),
        customer_id: cust, opened_at: stamp(hrs),
        time_to_resolve_mins: null,
        service_component: tpl[0], category: tpl[1],
        environment: 'production', site: site, priority: pri,
        root_cause_category: null, problem_id: null, kb_id: null,
        title: tpl[5], error_signature_raw: tpl[6],
        _generated: true
      });
    });

    /* Five deliberate issues per environment.
       The random tail above draws templates uniformly, so on any given day
       an environment can come up nearly empty and its health gauge shows
       100/100 for the wrong reason — no data reads identically to no
       problems. These are fixed so all three gauges always have something
       real behind them, and they are spread across severities and states so
       each environment shows a plausible mix rather than a wall of P1s.

       Columns: component, category, root cause, title, signature,
                priority, hours ago, minutes to resolve (null = still open) */
    const ENV_SEED = {
      flex: [
        ['nova-compute', 'compute', 'config-drift', 'Scheduler marks two computes down after patching', 'nova-compute State: down Updated_At older than service_down_time', '2', 5, 165],
        ['ceph', 'storage', 'capacity', 'SSD pool crosses nearfull on DC1', 'HEALTH_WARN pool nearfull POOL_NEAR_FULL', '2', 11, null],
        ['neutron-ovn', 'networking', 'design-limitation', 'Port binding slow during bulk policy push', 'ovn-northd Raft leader election; port binding timed out', '3', 26, 240],
        ['octavia', 'networking', 'unknown', 'Listener stuck provisioning on the ingest LB', 'listener stuck in PENDING_UPDATE provisioning_status', '3', 40, null],
        ['glance', 'image-guest', 'human-error', 'Image upload rejected on checksum', 'image upload failed checksum mismatch', '4', 62, 55]
      ],
      opsc: [
        ['keystone', 'identity', 'software-defect', 'Freshly issued tokens rejected by the API', '401 Unauthorized token revoked', '1', 3, null],
        ['opencenter-api', 'availability', 'config-drift', 'OpenCenter API 5xx burst behind the load balancer', 'HTTP 500 Internal Server Error haproxy server is DOWN', '2', 9, 195],
        ['horizon', 'identity', 'config-drift', 'Dashboard 500 after IdP redirect', 'Internal Server Error /auth/websso/', '3', 20, 90],
        ['mysql-galera', 'availability', 'third-party', 'Galera node desyncs under write load', 'wsrep_local_recv_queue growing node desynced', '2', 34, null],
        ['opencenter-cli', 'identity', 'works-as-designed', 'CLI reports 404 on a token that exists', "404 Could not find token", '4', 55, 35]
      ],
      /* Flex AI carries a deliberately fuller set. Accelerator estates fail in
         ways the IaaS fabric simply does not have — Xid faults, ECC row
         remapping, NVLink drops, thermal capping, collective-op timeouts — and
         the whole argument for giving Flex AI its own gauge is that those
         failures are invisible when averaged into Flex. The signatures are the
         real ones an engineer would grep for.

         They also form one causal chain rather than five unrelated faults:
         GPUs lost to Xid/ECC  ->  fewer allocatable GPUs  ->  pods pending and
         the survivors thermally capped  ->  inference OOMs and times out.
         KARL should be able to see that, which it cannot do if the demo data
         is five independent coincidences. */
      flexai: [
        ['gpu-node', 'compute', 'hardware-fault', 'GPU falls off the bus mid-training-run', 'NVRM: Xid 79 GPU has fallen off the bus', '1', 6, null],
        // P1: no allocatable GPU stops every training job on the platform,
        // which is a service-down condition for an AI environment even though
        // the same signature on a general compute pool would be a P2.
        ['gpu-scheduler', 'compute', 'capacity', 'Training pods pending, no allocatable GPU', '0/12 nodes are available: 12 Insufficient nvidia.com/gpu', '1', 14, null],
        ['gpu-node', 'compute', 'hardware-fault', 'Uncorrectable ECC error takes a GPU out of service', 'NVRM: Xid 48 double-bit ECC error; row remap pending, GPU marked unhealthy', '2', 19, null],
        ['gpu-fabric', 'networking', 'hardware-fault', 'NVLink drop stalls multi-GPU all-reduce', 'NCCL WARN NET/IB: got completion with error 12; NCCL timeout in ncclAllReduce', '2', 23, 275],
        ['model-serving', 'availability', 'capacity', 'Inference OOM under concurrent load', 'CUDA out of memory. Tried to allocate 2.00 GiB', '2', 28, 310],
        ['gpu-node', 'compute', 'capacity', 'Sustained thermal cap halves training throughput', 'nvidia-smi: SW Thermal Slowdown Active; clocks capped at 810 MHz, GPU temp 88C', '3', 33, null],
        ['gpu-scheduler', 'compute', 'capacity', 'GPU queue saturated, jobs waiting over an hour', 'DCGM_FI_DEV_GPU_UTIL 100% sustained; scheduler queue depth 47, oldest pending 68m', '2', 38, null],
        ['inference-gateway', 'availability', 'config-drift', 'Inference requests time out at the gateway', 'upstream request timeout while awaiting model response', '3', 45, 120],
        ['gpu-driver', 'compute', 'config-drift', 'Driver/CUDA mismatch after node reboot', 'CUDA driver version is insufficient for CUDA runtime version; nvidia-smi has failed', '3', 58, 80],
        ['vector-store', 'storage', 'capacity', 'Embedding index rebuild exceeds its window', 'index build aborted: memory limit exceeded during HNSW insert', '3', 70, null]
      ]
    };

    let sn = 0;
    const SEED_CUSTOMERS = ['hn-bank', 'hcmc-commerce', 'dc1-shared', 'dc2-shared', 'multisite'];
    Object.keys(ENV_SEED).forEach(env => {
      ENV_SEED[env].forEach((row, i) => {
        const [comp, cat, rc, title, sig, pri, hrs, ttr] = row;
        const cust = SEED_CUSTOMERS[(sn + i) % SEED_CUSTOMERS.length];
        const custRec = CUSTOMERS.find(c => c.customer_id === cust);
        out.push({
          ticket_id: 'INC' + String(9800 + sn).padStart(7, '0'),
          customer_id: cust,
          opened_at: stamp(hrs),
          time_to_resolve_mins: ttr,
          service_component: comp, category: cat,
          environment: 'production',
          site: custRec ? custRec.sites[0] : 'HN',
          priority: pri,
          // Only a closed case has a settled cause. Leaving it on an open one
          // would inflate the "issue types" breakdown with conclusions nobody
          // has actually reached yet.
          root_cause_category: ttr == null ? null : rc,
          problem_id: null, kb_id: null,
          title: title, error_signature_raw: sig,
          _generated: true, _env_seed: env
        });
        sn++;
      });
    });

    return out;
  }

  /* ---------------------------------------------------------------------
   * Analytics for the operations dashboard
   * ------------------------------------------------------------------- */
  const RANGES = {
    '1d': { label: 'Last 24 hours', days: 1, buckets: 12, unit: 'h' },
    '3d': { label: 'Last 3 days', days: 3, buckets: 12, unit: 'h' },
    '7d': { label: 'Last 7 days', days: 7, buckets: 7, unit: 'd' },
    '30d': { label: 'Last 30 days', days: 30, buckets: 15, unit: 'd' },
    'all': { label: 'All time', days: 400, buckets: 14, unit: 'd' }
  };

  function caseTime(c) {
    const d = new Date(String(c.opened_at).replace(' ', 'T'));
    return isNaN(d.getTime()) ? 0 : d.getTime();
  }

  const Analytics = {
    RANGES,

    inRange(key) {
      const r = RANGES[key] || RANGES['7d'];
      const cutoff = Date.now() - r.days * 86400000;
      return global.CASE_STORE.filter(c => caseTime(c) >= cutoff);
    },

    /** Time-bucketed opened/solved counts for the trend chart. */
    trend(key) {
      const r = RANGES[key] || RANGES['7d'];
      const now = Date.now();
      const span = r.days * 86400000;
      const size = span / r.buckets;
      const buckets = [];
      for (let i = 0; i < r.buckets; i++) {
        const start = now - span + i * size;
        buckets.push({ start, end: start + size, opened: 0, solved: 0, label: '' });
      }
      global.CASE_STORE.forEach(c => {
        const t = caseTime(c);
        const i = Math.floor((t - (now - span)) / size);
        if (i >= 0 && i < buckets.length) {
          buckets[i].opened++;
          if (c.time_to_resolve_mins != null) buckets[i].solved++;
        }
      });
      buckets.forEach(b => {
        const d = new Date(b.start);
        const pad = x => String(x).padStart(2, '0');
        b.label = r.unit === 'h' ? pad(d.getHours()) + ':00'
                                 : pad(d.getDate()) + '/' + pad(d.getMonth() + 1);
      });
      return { range: r, buckets };
    },

    topBy(key, field, limit) {
      const agg = {};
      this.inRange(key).forEach(c => {
        const v = c[field];
        if (!v) return;
        const a = agg[v] = agg[v] || { key: v, count: 0, open: 0, mins: 0, p1: 0 };
        a.count++;
        if (c.time_to_resolve_mins == null) a.open++; else a.mins += c.time_to_resolve_mins;
        if (c.priority === '1') a.p1++;
      });
      return Object.values(agg).sort((a, b) => b.count - a.count).slice(0, limit || 10);
    },

    counts(key) {
      const list = this.inRange(key);
      const solved = list.filter(c => c.time_to_resolve_mins != null);
      const pending = list.filter(c => c.time_to_resolve_mins == null);
      const mins = solved.reduce((s, c) => s + c.time_to_resolve_mins, 0);
      const problems = Problems.stats();
      return {
        total: list.length,
        pending: pending.length,
        solved: solved.length,
        p1_open: pending.filter(c => c.priority === '1').length,
        mttr: solved.length ? Math.round(mins / solved.length) : null,
        wad: list.filter(c => c.root_cause_category === 'works-as-designed').length,
        open_problems: problems.open,
        recurrences: problems.recurrences,
        solve_rate: list.length ? Math.round(solved.length / list.length * 100) : 0
      };
    },

    /**
     * 0-100 composite health score with the reasons that moved it.
     *
     * Deliberately RANGE-AWARE. Two indicators are meaningless on short
     * windows and must not be applied there:
     *   - solve rate: cases opened in the last 24h are mostly still open by
     *     definition, so scoring a 1-day window on it punishes recency;
     *   - recurrence count: it is an all-time property of the Problem
     *     register, not something that happened inside the window.
     */
    health(key) {
      const days = (RANGES[key] || RANGES['7d']).days;
      const c = this.counts(key);
      const comps = this.topBy(key, 'service_component', 3);
      let score = 100;
      const factors = [];
      const hit = (pts, why) => { score -= pts; factors.push({ pts, why }); };

      // Always relevant: unresolved critical work.
      if (c.p1_open > 0) hit(Math.min(24, c.p1_open * 8), c.p1_open + ' P1 incident(s) still open');

      // Throughput — only where closure is a fair expectation.
      if (days >= 7 && c.total > 5 && c.solve_rate < 60) {
        hit(12, 'Solve rate ' + c.solve_rate + '% over ' + days + ' days — backlog is building');
      }
      if (days >= 3 && c.mttr && c.mttr > 240) {
        hit(10, 'MTTR ' + c.mttr + ' min is above the 240 min target');
      }

      // Structural debt — an all-time property, so only on long windows.
      if (c.open_problems > 2) hit(8, c.open_problems + ' Problems open without a permanent fix');
      if (days >= 30 && c.recurrences > 4) {
        hit(10, c.recurrences + ' recurrences on the Problem register — fixes are not sticking');
      }

      // Concentration: one component carrying a disproportionate share.
      if (comps[0] && c.total >= 8 && comps[0].count > c.total * 0.3) {
        hit(8, comps[0].key + ' accounts for ' + comps[0].count + ' of ' + c.total +
               ' cases — concentrated risk');
      }
      if (c.total > 8 && c.wad > c.total * 0.15) {
        hit(5, c.wad + ' works-as-designed tickets — documentation gap consuming support time');
      }

      score = Math.max(0, Math.min(100, score));
      return {
        score,
        band: score >= 80 ? 'healthy' : score >= 55 ? 'degraded' : 'at-risk',
        factors, counts: c, hotspots: comps, window_days: days
      };
    }
  };

  Cases.ingest = ingestCase;
  Cases.generateRecent = generateRecentCases;
  global.Analytics = Analytics;

  // Seed the recent tail once, at load.
  generateRecentCases(64).forEach(ingestCase);
  global.KT_DATA = { Customers, Cases, Problems, CASE_KEY, PROBLEM_KEY, saveStore, ingestCase };
})(window);
