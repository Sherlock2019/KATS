/* =============================================================================
 * kt_rag.js — browser client for the KATS RAG backend.
 *
 * The backend (rag/backend, FastAPI + PostgreSQL/pgvector + Ollama) is
 * OPTIONAL. This file is written so that everything degrades to the existing
 * offline behaviour the moment it is not reachable:
 *
 *   endpoint unset / unreachable  ->  every call resolves to null, never throws
 *   endpoint reachable            ->  tickets are ingested, chat answers from
 *                                     the vector store
 *
 * That is deliberate: the single-file demo's whole claim is "0 external
 * requests, works offline". Wiring a database in must not cost that. Same
 * switch the KB API endpoint in §10.0.2 already uses.
 *
 * Load AFTER kt_record.js.
 *
 * Exposes: RAG
 * ========================================================================== */
(function (global) {
  'use strict';

  const CFG_KEY = 'kt_rag_config_v1';
  const DEFAULT_ENDPOINT = 'http://127.0.0.1:8001';

  const CONFIG = {
    endpoint: '',
    enabled: false,
    /* Filled in by health(); the UI shows these so nobody has to guess which
       model answered. */
    status: 'unknown',      // unknown | up | down
    llm_model: '',
    embed_model: '',
    tickets: null,
    detail: ''
  };

  function load() {
    let raw = null;
    try { raw = JSON.parse(localStorage.getItem(CFG_KEY) || 'null'); } catch (e) {}
    if (raw && typeof raw === 'object') {
      CONFIG.endpoint = String(raw.endpoint || '');
      CONFIG.enabled = !!raw.enabled;
    }
    return CONFIG;
  }

  function save() {
    try {
      localStorage.setItem(CFG_KEY, JSON.stringify({
        endpoint: CONFIG.endpoint, enabled: CONFIG.enabled
      }));
    } catch (e) {}
  }

  function configure(opts) {
    const o = opts || {};
    if ('endpoint' in o) CONFIG.endpoint = String(o.endpoint || '').replace(/\/+$/, '');
    if ('enabled' in o) CONFIG.enabled = !!o.enabled;
    if (CONFIG.enabled && !CONFIG.endpoint) CONFIG.endpoint = DEFAULT_ENDPOINT;
    CONFIG.status = 'unknown';
    save();
    return CONFIG;
  }

  const live = () => CONFIG.enabled && !!CONFIG.endpoint;

  /* Every call goes through here. It never rejects: a RAG backend that is
     down must degrade the page, not break it. */
  function call(path, opts) {
    if (!live()) return Promise.resolve(null);
    const o = opts || {};
    const ctl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    const timer = ctl ? setTimeout(() => ctl.abort(), o.timeout || 30000) : null;

    return fetch(CONFIG.endpoint + path, {
      method: o.method || 'GET',
      headers: o.body ? { 'Content-Type': 'application/json' } : undefined,
      body: o.body ? JSON.stringify(o.body) : undefined,
      signal: ctl ? ctl.signal : undefined
    })
      .then(r => {
        if (!r.ok) return r.text().then(t => Promise.reject(new Error('HTTP ' + r.status + ' ' + t.slice(0, 200))));
        return r.json();
      })
      .catch(e => {
        CONFIG.status = 'down';
        CONFIG.detail = (e && e.name === 'AbortError') ? 'timed out' : (e && e.message) || 'unreachable';
        return null;
      })
      .then(v => { if (timer) clearTimeout(timer); return v; });
  }

  /** Is the stack up, and which models is it running? */
  function health() {
    if (!live()) {
      CONFIG.status = 'unknown';
      CONFIG.detail = 'local-only mode — no endpoint configured';
      return Promise.resolve(null);
    }
    return call('/health', { timeout: 5000 }).then(r => {
      if (!r) return null;
      CONFIG.status = 'up';
      CONFIG.llm_model = r.llm_model || '';
      CONFIG.embed_model = r.embed_model || '';
      CONFIG.tickets = (r.counts && r.counts.tickets) != null ? r.counts.tickets : null;
      CONFIG.detail = r.ollama ? 'ollama ok' : 'ollama unreachable — chat will fail';
      return r;
    });
  }

  /**
   * Upsert one ticket. `sub` is an Intake submission or a support-side
   * { ticket_id, customer_id, opened_at, fields } object; Record.toRagDoc()
   * does the PII stripping and the chunking.
   */
  function ingest(sub, extra) {
    if (!live()) return Promise.resolve(null);
    if (!global.Record) return Promise.resolve(null);
    const doc = global.Record.toRagDoc(sub, extra);
    if (!doc.ticket_id || !doc.customer_id) return Promise.resolve(null);
    return call('/tickets', { method: 'POST', body: doc, timeout: 60000 });
  }

  /**
   * Hybrid retrieval. `customer_id` is required for anything customer-facing;
   * support may pass null to search the whole fleet.
   */
  function search(question, opts) {
    const o = opts || {};
    return call('/search', {
      method: 'POST',
      timeout: 30000,
      body: {
        question: String(question || ''),
        customer_id: o.customer_id || null,
        doc_types: o.doc_types || ['intake', 'resolution', 'kb'],
        facets: o.facets || {},
        top_k: o.top_k || 8
      }
    });
  }

  /** Retrieval + local LLM. Returns { answer, evidence[], model, ms }. */
  function chat(question, opts) {
    const o = opts || {};
    return call('/chat', {
      method: 'POST',
      timeout: o.timeout || 180000,
      body: {
        question: String(question || ''),
        customer_id: o.customer_id || null,
        doc_types: o.doc_types || ['intake', 'resolution', 'kb'],
        facets: o.facets || {},
        top_k: o.top_k || 8,
        history: o.history || []
      }
    });
  }

  /**
   * Streaming chat. Same answer as chat(), delivered as it is written.
   *
   * On a CPU-only laptop a grounded answer takes over a minute end to end.
   * Streaming does not make that faster — it makes it watchable: evidence
   * lands in under a second, the first sentence a few seconds later.
   *
   * @param handlers { onEvidence(list), onToken(text), onDone(meta), onError(detail) }
   * @returns a promise resolving to { answer, evidence, model, ms } or null.
   */
  function chatStream(question, opts, handlers) {
    if (!live()) return Promise.resolve(null);
    const o = opts || {};
    const h = handlers || {};

    const body = {
      question: String(question || ''),
      customer_id: o.customer_id || null,
      doc_types: o.doc_types || ['intake', 'resolution', 'kb'],
      facets: o.facets || {},
      top_k: o.top_k || 6,
      history: o.history || []
    };

    return fetch(CONFIG.endpoint + '/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(res => {
      if (!res.ok || !res.body) {
        return Promise.reject(new Error('HTTP ' + res.status));
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      const out = { answer: '', evidence: [], model: '', ms: 0, embed_mode: '' };
      let buffer = '';
      let failed = null;

      /* NDJSON: a chunk can split a line anywhere, so keep the tail in the
         buffer until a newline actually arrives. */
      const consume = text => {
        buffer += text;
        const lines = buffer.split('\n');
        buffer = lines.pop();
        lines.forEach(line => {
          if (!line.trim()) return;
          let msg;
          try { msg = JSON.parse(line); } catch (e) { return; }
          if (msg.type === 'evidence') {
            out.evidence = msg.evidence || [];
            if (h.onEvidence) h.onEvidence(out.evidence);
          } else if (msg.type === 'token') {
            out.answer += msg.t;
            if (h.onToken) h.onToken(msg.t, out.answer);
          } else if (msg.type === 'done') {
            out.model = msg.model; out.ms = msg.ms; out.embed_mode = msg.embed_mode;
          } else if (msg.type === 'error') {
            failed = msg.detail;
          }
        });
      };

      const pump = () => reader.read().then(({ done, value }) => {
        if (done) {
          if (buffer.trim()) consume('\n');
          if (failed) { if (h.onError) h.onError(failed); return null; }
          if (h.onDone) h.onDone(out);
          return out;
        }
        consume(decoder.decode(value, { stream: true }));
        return pump();
      });

      return pump();
    }).catch(e => {
      CONFIG.status = 'down';
      CONFIG.detail = (e && e.message) || 'stream failed';
      if (h.onError) h.onError(CONFIG.detail);
      return null;
    });
  }

  /** Bulk-load everything already in the browser. Used by the admin button. */
  function backfill(onProgress) {
    if (!live()) return Promise.resolve({ sent: 0, failed: 0, skipped: 'not configured' });
    const queue = (global.Intake && global.Intake.queue) ? global.Intake.queue() : [];
    let sent = 0, failed = 0;
    return queue.reduce((p, sub) => p.then(() =>
      ingest(sub).then(r => {
        if (r) sent++; else failed++;
        if (onProgress) onProgress(sent + failed, queue.length);
      })
    ), Promise.resolve()).then(() => ({ sent, failed, total: queue.length }));
  }

  load();

  global.RAG = {
    CONFIG, DEFAULT_ENDPOINT,
    configure, health, ingest, search, chat, chatStream, backfill,
    isLive: live
  };
})(window);
