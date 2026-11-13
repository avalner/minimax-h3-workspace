/* One owner for the job records and their results.
 *
 * The page used to keep this in a plain `QUEUE` array with `qAdd`/`qHandle` and let every section
 * mutate a record it had found with `QUEUE.find(...)`. That worked, but it meant the queue had as
 * many owners as it had call sites: a stale `find` could change a record after the server had moved
 * on, and the "what did this job finish with" fact lived only inside those records.
 *
 * Here the queue is a map by prompt id (insertion order = queue order), and the outcome of a job is
 * remembered separately by the same id. The page reads snapshots and asks for changes; it never
 * writes a record itself. Results are published once through `onResult` (drafts and the Library stay
 * the page's own collections), so a repeated event or a snapshot after a reconnect cannot add the
 * same result twice.
 *
 * This file is a classic script, not an ES module: the page builds its first preview panels while it
 * is still being parsed, and `qAdd` is called from those panels. A deferred module would be loaded
 * after them. It publishes `window.h3State`, the same way api.js/events.js publish `h3Api`/`h3Events`.
 *
 * Contract (everything else is private):
 *   configure({ onResult, onChange })   callbacks; onResult receives a normalized result item
 *   addJob({ id, mode, prompt, project, detached, agent }) -> record (throws on an empty id)
 *   handle(message) -> record | null     a queue event (`stage`/`done`/`error`/`interrupted`)
 *   resync(hello) -> [record]            the first message of a socket: jobs + recent outcomes
 *   get(id) / has(id) / list() / active() / running() / finished() / count() / position(record)
 *   update(id, patch) -> record | null    detached/agent/msg flags; unknown keys are ignored
 *   markStopping(id) -> record | null
 *   removeFinished() -> number
 *   outcome(id) / results() / result(id)
 *   subscribe(fn) -> unsubscribe         called after every change
 *
 * A `handle`/`resync` may deliver results before the job is known (another program's job): the
 * result is still published, the record simply is not there.
 */

(function () {
  "use strict";

  const FIELDS = ["mode", "prompt", "state", "stage", "step", "max", "scene", "nscenes", "t0",
                  "took", "itemId", "preview", "detached", "agent", "stopping", "msg",
                  "lastEv", "stepT", "step1T", "project"];

  const jobs = new Map();       // prompt id -> record, insertion order is the queue
  const outcomes = new Map();   // prompt id -> how the last run ended
  const results = new Map();    // media id -> the item a job produced
  const subscribers = new Set();
  let hooks = { onResult: noop, onChange: noop };
  let seq = 0;

  function noop() {}

  function now() {
    return typeof performance !== "undefined" && performance.now ? performance.now() : Date.now();
  }

  function notify(change) {
    subscribers.forEach(fn => { try { fn(change); } catch (e) { console.error(e); } });
    try { hooks.onChange(change); } catch (e) { console.error(e); }
  }

  /* A result item is what a section shows: the Library uses a Date, the server sends a string.
     The copy keeps the page from editing the item it got from the wire. */
  function item(raw) {
    if (!raw || typeof raw !== "object") return null;
    const date = raw.date instanceof Date ? raw.date : raw.date ? new Date(raw.date) : new Date();
    return { ...raw, date: isNaN(date.getTime()) ? new Date() : date };
  }

  function publish(raw, via) {
    const it = item(raw);
    if (!it) return null;
    if (it.id) results.set(it.id, it);
    if (results.size > 500) results.delete(results.keys().next().value);
    try { hooks.onResult(it, via || "event"); } catch (e) { console.error(e); }
    return it;
  }

  function trim() {
    if (outcomes.size <= 200) return;
    const drop = outcomes.size - 200;
    const keys = [...outcomes.keys()].slice(0, drop);
    keys.forEach(k => outcomes.delete(k));
  }

  function make({ id, mode, prompt, project = "", detached = false, agent = false } = {}) {
    if (!id) throw new Error("state: a job needs an id");
    return {
      id, mode: mode || "", prompt: prompt || "",
      state: "queued", stage: "queued", step: null, max: null, scene: null, nscenes: null,
      t0: null, took: 0, itemId: null, preview: false,
      project: project || "",   // the project the job was submitted in (the queue is shared)
      detached: !!detached, agent: !!agent, stopping: null, msg: "", lastEv: now(),
      seq: ++seq,
    };
  }

  function addJob(options) {
    const j = make(options);
    const old = jobs.get(j.id);
    if (old) return old;                 // one id, one record: a repeated event must not fork it
    jobs.set(j.id, j);
    notify({ type: "add", record: j });
    return j;
  }

  function get(id) {
    return jobs.get(id) || null;
  }

  function has(id) {
    return jobs.has(id);
  }

  function list() {
    return [...jobs.values()];
  }

  function active() {
    return list().filter(j => j.state === "queued" || j.state === "running");
  }

  function running() {
    return list().find(j => j.state === "running") || null;
  }

  function finished() {
    return list().filter(j => !(j.state === "queued" || j.state === "running"));
  }

  function position(j) {
    return active().indexOf(j);
  }

  function update(id, patch) {
    const j = jobs.get(id);
    if (!j || !patch) return j || null;
    FIELDS.forEach(k => { if (k in patch) j[k] = patch[k]; });
    return j;
  }

  function markStopping(id) {
    const j = update(id, { stopping: now() });
    if (j) notify({ type: "update", record: j });
    return j;
  }

  /* a heartbeat from the engine's preview stream: the queue needs it to tell "running" from "stuck" */
  function touch(id) {
    const j = jobs.get(id);
    if (j) j.lastEv = now();
    return j || null;
  }

  function rememberOutcome(j, kind) {
    outcomes.set(j.id, {
      state: kind === "error" ? "error" : kind === "interrupted" ? "interrupted" : "done",
      message: j.msg || "",
      itemId: j.itemId || "",
      preview: !!j.preview,
      took: j.took || 0,
      at: Date.now(),
    });
    trim();
  }

  function handle(m) {
    if (!m || !m.prompt_id) return null;
    const j = jobs.get(m.prompt_id) || null;
    if (m.type === "done") {
      const items = (m.items || [m.item]).filter(Boolean).map(raw => publish(raw, "event")).filter(Boolean);
      if (!j) return null;                       // a job of another program: the result is enough
      j.lastEv = now();
      j.state = "done";
      j.stopping = null;
      j.took = j.t0 ? (now() - j.t0) / 1000 : 0;
      j.itemId = (m.item || items[0] || {}).id || null;
      j.preview = !!items.length && items.every(it => it.preview);
      rememberOutcome(j, "done");
    } else if (m.type === "stage") {
      if (!j) return null;
      const sampling = String(m.stage || "").startsWith("sample");
      if (sampling && (j.stage !== m.stage || (m.scene && j.scene !== m.scene))) j.step1T = null;
      if (sampling && m.step) {
        j.stepT = now();
        if (m.step === 1) j.step1T = j.stepT;   // the first step includes loading, it is not typical
      }
      j.lastEv = now();
      j.scene = m.scene; j.nscenes = m.scenes;
      j.state = "running"; j.stage = m.stage; j.step = m.step; j.max = m.max;
      j.t0 = j.t0 || now();
    } else if (m.type === "error") {
      if (!j) return null;
      j.lastEv = now(); j.state = "error"; j.msg = m.message || ""; j.stopping = null;
      rememberOutcome(j, "error");
    } else if (m.type === "interrupted") {
      if (!j) return null;
      j.lastEv = now(); j.state = "stopped"; j.stopping = null;
      rememberOutcome(j, "interrupted");
    } else {
      return null;
    }
    notify({ type: m.type, record: j, message: m });
    return j;
  }

  /* The first message of every connection: what the window missed while it was away. A snapshot,
     not a journal — a repeated event or a missing one cannot leave a row wrong, because the snapshot
     is applied as a whole and the outcomes are the facts the server kept. */
  function resync(hello) {
    const snap = hello || {};
    const live = new Set();
    const byJob = new Map();
    (snap.jobs || []).forEach(raw => {
      if (!raw || !raw.prompt_id) return;
      live.add(raw.prompt_id);
      let j = jobs.get(raw.prompt_id);
      if (!j) {
        j = make({ id: raw.prompt_id, mode: raw.mode, prompt: raw.prompt || "",
                   project: raw.project || "", detached: !!raw.agent, agent: !!raw.agent });
        jobs.set(j.id, j);
      }
      if (raw.project) j.project = raw.project;   // the server keeps the project the job was stamped with
      if (raw.state === "running") {
        j.state = "running"; j.stage = raw.stage || j.stage || "queued";
        j.step = raw.step; j.max = raw.max;
        j.t0 = j.t0 || now();
      } else {
        j.state = "queued";
      }
    });
    (snap.results || []).forEach(raw => {
      const it = publish(raw, "snapshot");
      if (it && it.prompt_id) byJob.set(it.prompt_id, it);
    });
    (snap.recent || []).forEach(r => {
      if (!r || !r.prompt_id) return;
      outcomes.set(r.prompt_id, { state: r.state || "done", message: r.message || "",
                                  at: Date.now(), itemId: r.item_id || "", preview: false, took: 0 });
    });
    trim();
    const changed = [];
    jobs.forEach(j => {
      if (j.state !== "queued" && j.state !== "running") return;
      if (live.has(j.id)) return;               // still running somewhere (another window, the agent)
      const res = byJob.get(j.id);
      const rec = outcomes.get(j.id);
      if (res) {                                 // a finished result is the strongest fact
        j.state = "done"; j.itemId = res.id; j.preview = !!res.preview;
        changed.push(j);
        return;
      }
      if (!rec) return;
      j.state = rec.state === "error" ? "error" : rec.state === "interrupted" ? "stopped" : "done";
      j.msg = rec.message || j.msg || "";
      j.itemId = rec.itemId || j.itemId;
      j.preview = !!rec.preview;
      j.took = rec.took || j.took;
      changed.push(j);
    });
    notify({ type: "resync", records: changed });
    return changed;
  }

  function removeFinished() {
    let n = 0;
    for (const [id, j] of jobs) {
      if (j.state !== "queued" && j.state !== "running") { jobs.delete(id); n++; }
    }
    if (n) notify({ type: "clear" });
    return n;
  }

  function outcome(id) {
    return outcomes.get(id) || null;
  }

  function result(id) {
    return results.get(id) || null;
  }

  function resultList() {
    return [...results.values()];
  }

  function configure(next) {
    hooks = { onResult: (next && next.onResult) || noop, onChange: (next && next.onChange) || noop };
  }

  function subscribe(fn) {
    if (typeof fn !== "function") return noop;
    subscribers.add(fn);
    return () => subscribers.delete(fn);
  }

  window.h3State = {
    configure, subscribe,
    addJob, get, has, list, active, running, finished, count: () => jobs.size,
    position, update, markStopping, touch, removeFinished,
    handle, resync, outcome, result, results: resultList,
  };
})();
