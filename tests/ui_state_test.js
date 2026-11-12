#!/usr/bin/env node
/* Unit checks for app/ui/js/state.js — the one owner of job records and their outcomes.
 *
 *     node tests/ui_state_test.js
 *
 * The module is a classic script that publishes `window.h3State`, so it is loaded into a `vm`
 * context with a fake `window`. Nothing here touches the DOM: the page's own render callbacks are
 * counters, which is exactly the contract — the state must not need the page to stay correct.
 *
 * Exit code: 0 all checks passed, 1 something failed.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "..", "app", "ui", "js", "state.js"), "utf8");
const sandbox = { window: {}, performance, console, Date, Map, Set, JSON, Math, isNaN, Error };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: "state.js" });
const S = sandbox.window.h3State;

let failed = 0;
function check(name, ok, detail) {
  console.log((ok ? "ok    " : "FAIL  ") + name + (!ok && detail ? "   [" + detail + "]" : ""));
  if (!ok) failed++;
}
function eq(name, actual, expected) {
  check(name, JSON.stringify(actual) === JSON.stringify(expected), "получено " + JSON.stringify(actual));
}

if (!S) {
  console.log("FAIL  state.js publishes window.h3State");
  process.exit(1);
}
check("state.js publishes window.h3State", true);

const results = [], changes = [];
S.configure({ onResult: (it, via) => results.push([it, via]), onChange: c => changes.push(c) });

/* one id, one record */
const a = S.addJob({ id: "j1", mode: "video", prompt: "кот" });
const a2 = S.addJob({ id: "j1", mode: "video", prompt: "кот" });
check("a repeated add does not fork the record", a === a2 && S.count() === 1, S.list());
eq("the record starts queued", [a.state, a.stage], ["queued", "queued"]);
check("the page cannot add a job without an id", (() => {
  try { S.addJob({ mode: "video" }); return false; } catch { return true; }
})());

/* a stage event moves the record and keeps the time of the first one */
S.handle({ type: "stage", prompt_id: "j1", stage: "sample", step: 3, max: 20 });
a.lastEv = 0;
S.handle({ type: "stage", prompt_id: "j1", stage: "sample", step: 4, max: 20 });
eq("a stage event makes the job running", [S.get("j1").state, S.get("j1").step], ["running", 4]);
check("the start time is not reset by later stages", S.get("j1").t0 !== null);

/* a done event publishes each result exactly once, with a Date */
S.handle({ type: "done", prompt_id: "j1", item: { id: "m1", url: "/x", date: "2026-11-12T10:00:00" } });
eq("a done event publishes its result once", results.map(r => r[0].id), ["m1"]);
check("the published result has a Date", results[0][0].date instanceof Date);
eq("a done event records the outcome", [S.get("j1").state, S.get("j1").itemId], ["done", "m1"]);
check("the outcome is kept by id", S.outcome("j1") && S.outcome("j1").state === "done");
check("the result is kept by id", S.result("m1") && S.result("m1").id === "m1");

/* the same done event again: the page's own collections decide about the item, state does not
   count a second result into the index (the map is keyed by the media id) */
S.handle({ type: "done", prompt_id: "j1", item: { id: "m1", url: "/x", date: "2026-11-12T10:00:00" } });
eq("a repeated done keeps one result by id", S.results().length, 1);

/* an event for an unknown job: the result still matters, the record does not appear */
S.handle({ type: "done", prompt_id: "other", item: { id: "m2", preview: true, date: "2026-11-12T10:00:00" } });
check("a result of an unknown job is published", results.some(r => r[0].id === "m2"));
check("an unknown job does not appear in the queue", !S.has("other"));

/* error and interrupted are outcomes too */
S.addJob({ id: "j2", mode: "video" });
S.handle({ type: "error", prompt_id: "j2", message: "нет памяти" });
eq("an error event records its message", [S.get("j2").state, S.get("j2").msg], ["error", "нет памяти"]);
S.addJob({ id: "j3", mode: "video" });
S.handle({ type: "interrupted", prompt_id: "j3" });
eq("an interrupted event is a stopped job", S.get("j3").state, "stopped");

/* the snapshot: a job that finished while the window was away is reconciled from its outcome,
   and a result is the strongest fact for a job without one */
results.length = 0;
S.addJob({ id: "j4", mode: "video" });
S.addJob({ id: "j5", mode: "video" });
S.addJob({ id: "j6", mode: "video" });
const changed = S.resync({
  jobs: [{ prompt_id: "j6", state: "running", stage: "sample", step: 2, max: 10, mode: "video" }],
  recent: [
    { prompt_id: "j4", state: "done", message: "" },
    { prompt_id: "j5", state: "error", message: "сорвалось" },
  ],
  results: [{ id: "m4", prompt_id: "j4", url: "/4", date: "2026-11-12T10:00:00" }],
});
eq("the snapshot publishes its results", results.map(r => r[0].id), ["m4"]);
eq("the snapshot marks a missing job done from its result",
  [S.get("j4").state, S.get("j4").itemId], ["done", "m4"]);
eq("the snapshot marks a missing job failed from its outcome", S.get("j5").state, "error");
check("a job that is still running elsewhere is left alone", S.get("j6").state === "running");
check("the snapshot reports what it changed", changed.map(j => j.id).sort().join(",") === "j4,j5", changed.map(j => j.id));

/* a repeated snapshot must not duplicate results */
results.length = 0;
S.resync({ jobs: [], recent: [], results: [{ id: "m4", prompt_id: "j4", url: "/4", date: "2026-11-12T10:00:00" }] });
eq("a repeated snapshot publishes one result again", results.length, 1);
eq("results are indexed by media id, not by delivery", S.results().map(x => x.id).sort(), ["m1", "m2", "m4"]);

/* page-side changes go through update; unknown fields are ignored */
S.update("j1", { detached: true, agent: true, state: "queued", nonsense: 1 });
eq("update changes only known fields", [S.get("j1").detached, S.get("j1").agent, S.get("j1").state],
   [true, true, "queued"]);
check("update of an unknown id is null", S.update("nope", {}) === null);

/* stop request and the finished rows */
S.markStopping("j1");
check("a stop request is remembered on the record", !!S.get("j1").stopping);
eq("active jobs are the queued and running ones", S.active().map(j => j.id).sort(), ["j1", "j6"]);
const finishedBefore = S.finished().length;
const removed = S.removeFinished();
check("finished rows are removed", removed === finishedBefore && S.finished().length === 0, { removed, finishedBefore });
eq("removing finished rows keeps the active ones", S.active().map(j => j.id).sort(), ["j1", "j6"]);

/* subscribers see a change with its type; a throwing subscriber does not break the state */
let seen = [];
const off = S.subscribe(c => seen.push(c && c.type));
S.addJob({ id: "j7", mode: "video" });
S.handle({ type: "stage", prompt_id: "j7", stage: "load", step: 0 });
off();
S.addJob({ id: "j8", mode: "video" });
eq("a subscriber sees adds and stages", seen.slice(0, 2), ["add", "stage"]);
check("an unsubscribed listener is not called", seen.length === 2, seen);
S.subscribe(() => { throw new Error("broken listener"); });
const loud = sandbox.console;
sandbox.console = { ...console, error() {} };   // the module reports the broken listener; that is expected here
try { S.handle({ type: "stage", prompt_id: "j7", stage: "encode", step: 0 }); check("a throwing subscriber does not break the state", true); }
catch (e) { check("a throwing subscriber does not break the state", false, e.message); }
sandbox.console = loud;

console.log(failed ? `\n${failed} failed` : "\nall state checks passed");
process.exit(failed ? 1 : 0);
