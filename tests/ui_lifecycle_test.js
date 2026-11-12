#!/usr/bin/env node
/* Unit checks for app/ui/js/lifecycle.js — a mode's resources have one owner.
 *
 *     node tests/ui_lifecycle_test.js
 *
 * The module talks to the DOM only through a handful of functions, so the test replaces them with
 * counters: a disposed scope must remove every listener, clear every timer, cancel every frame and
 * revoke every object URL exactly once — the contract that keeps a long session flat.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "..", "app", "ui", "js", "lifecycle.js"), "utf8");

let listeners = 0, intervals = 0, timeouts = 0, frames = 0, urls = 0, revoked = 0, cleared = 0;
const store = { intervals: new Map(), timeouts: new Map(), frames: new Map(), urls: new Set() };
let nextId = 0;

const sandbox = {
  window: {},
  console,
  URL: {
    createObjectURL: () => { urls++; const u = "blob:" + (++nextId); store.urls.add(u); return u; },
    revokeObjectURL: u => { if (store.urls.delete(u)) revoked++; },
  },
  setInterval: fn => { intervals++; const id = ++nextId; store.intervals.set(id, fn); return id; },
  clearInterval: id => { if (store.intervals.delete(id)) cleared++; intervals--; },
  setTimeout: fn => { timeouts++; const id = ++nextId; store.timeouts.set(id, fn); return id; },
  clearTimeout: id => { if (store.timeouts.delete(id)) cleared++; timeouts--; },
  requestAnimationFrame: fn => { frames++; const id = ++nextId; store.frames.set(id, fn); return id; },
  cancelAnimationFrame: id => { if (store.frames.delete(id)) cleared++; frames--; },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: "lifecycle.js" });
const L = sandbox.window.h3Lifecycle;

let failed = 0;
function check(name, ok, detail) {
  console.log((ok ? "ok    " : "FAIL  ") + name + (!ok && detail ? "   [" + detail + "]" : ""));
  if (!ok) failed++;
}

check("lifecycle.js publishes window.h3Lifecycle", !!L);
if (!L) { console.log("1 failed"); process.exit(1); }

/* a listener is added to the target and removed on dispose */
const target = {
  on: new Set(),
  addEventListener(type, fn) { listeners++; this.on.add(type + ":" + fn.name); },
  removeEventListener(type, fn) { if (this.on.delete(type + ":" + fn.name)) listeners--; },
};
const s1 = L.scope("test");
function handler() {}
s1.listen(target, "click", handler);
check("listen adds the handler", listeners === 1 && target.on.has("click:handler"));
s1.dispose();
check("dispose removes the handler", listeners === 0 && target.on.size === 0);
s1.dispose();
check("dispose is idempotent", listeners === 0 && s1.disposed);

/* timers and frames */
const s2 = L.scope("timers");
s2.interval(() => {}, 100);
s2.timeout(() => {}, 100);
s2.raf(() => {});
check("the scope starts its resources", intervals === 1 && timeouts === 1 && frames === 1);
s2.dispose();
check("dispose clears timers and frames", intervals === 0 && timeouts === 0 && frames === 0, `${intervals}/${timeouts}/${frames}`);

/* object URLs and arbitrary cleanups */
const s3 = L.scope("urls");
const u = s3.url({ blob: true });
check("a URL belongs to the scope", urls === 1 && store.urls.has(u));
let cleaned = false;
s3.add(() => { cleaned = true; });
s3.dispose();
check("dispose revokes the URL", revoked === 1 && !store.urls.has(u));
check("dispose runs a custom cleanup", cleaned);
check("a disposed scope has no cleanups left", s3.size === 0 && s3.counts.listen === 0 && s3.counts.interval === 0);

/* pauseMedia: every clip under the section is paused, and a throwing element does not stop it */
const paused = [];
const media = el => ({ pause() { paused.push(el); } });
const root = { querySelectorAll: () => [media("a"), media("b")] };
L.pauseMedia(root);
check("pauseMedia pauses every clip", paused.join(",") === "a,b", paused.join(","));
check("pauseMedia tolerates no section", (() => { try { L.pauseMedia(null); return true; } catch { return false; } })());

/* counts and disposeAll */
const s4 = L.scope("one"), s5 = L.scope("two");
s4.listen(target, "x", handler); s4.url({}); s5.interval(() => {}, 10);
const before = L.counts();
check("counts sees every live scope", before.scopes === 2 && before.listen >= 1 && before.interval >= 1, JSON.stringify(before));
L.disposeAll();
const after = L.counts();
check("disposeAll leaves nothing live", after.scopes === 0 && after.listen === 0 && after.interval === 0, JSON.stringify(after));
check("counts drops every resource", urls === revoked + 0 || store.urls.size === 0, `urls=${urls} revoked=${revoked} live=${store.urls.size}`);

console.log(failed ? `\n${failed} failed` : "\nall lifecycle checks passed");
process.exit(failed ? 1 : 0);
