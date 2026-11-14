/* Which section owns which resources, and what to give back when the user leaves it.
 *
 * The window keeps every section in the DOM and shows one by toggling `.active`. That is fine for
 * the markup; it is not fine for what a section starts while it is open — a frame loop, a poll, a
 * microphone, a playing clip. `register(id, def)` says:
 *
 *   mount(scope)   once, on the first visit: build the section's markup and wire its handlers
 *                  through `scope` (listeners, timers, object URLs)
 *   enter(scope)   every visit, after mount (optional)
 *   leave(scope)   every time another section is opened: pause playback, stop polls that only make
 *                  sense while visible, save a draft (optional)
 *   dispose()      optional extra release; the registry always disposes the scope itself
 *
 * `activate(id)` is called by the navigation after the classes are toggled. Mounting is once, so a
 * hundred visits cannot double a handler; leaving is real, so timers and media do not keep working
 * behind a hidden section. `pagehide` disposes everything, including object URLs.
 *
 * A classic script, loaded before the page: the page registers its modes while it is parsed.
 */

(function () {
  "use strict";

  const registry = new Map();
  let current = null;

  function register(id, def) {
    if (!id) throw new Error("modes: a mode needs an id");
    registry.set(id, { id, mounted: false, scope: null, ...(def || {}) });
  }

  function ensure(id) {
    const mode = registry.get(id);
    if (!mode) return null;
    if (!mode.mounted) {
      mode.scope = window.h3Lifecycle.scope(id);
      try {
        mode.mount && mode.mount(mode.scope);
        const root = document.getElementById('view-' + id);
        root?.querySelectorAll(':scope > .grid:has(> .preview)').forEach(grid => window.gridResize?.(grid));
        window.h3Components?.compactChoices(root, mode.scope);
      }
      catch (e) { console.error(`modes: ${id} did not mount`, e); }
      mode.mounted = true;
    }
    return mode;
  }

  function activate(id) {
    if (!id || id === current) { if (id) ensure(id); return; }
    const previous = registry.get(current);
    if (previous) {
      try { previous.leave && previous.leave(previous.scope); }
      catch (e) { console.error(`modes: ${previous.id} did not leave cleanly`, e); }
    }
    current = id;
    const next = ensure(id);
    if (next) {
      try { next.enter && next.enter(next.scope); }
      catch (e) { console.error(`modes: ${id} did not enter`, e); }
    }
  }

  /* mount without showing: the details window fills a section before it switches to it */
  function ready(id) {
    ensure(id);
    return registry.get(id) || null;
  }

  function disposeAll() {
    registry.forEach(mode => {
      try { mode.leave && mode.leave(mode.scope); } catch (e) { console.error(e); }
      try { mode.dispose && mode.dispose(mode.scope); } catch (e) { console.error(e); }
      if (mode.scope) mode.scope.dispose();
      mode.scope = null;
      mode.mounted = false;
    });
    current = null;
    window.h3Lifecycle.disposeAll();
  }

  function stats() {
    const modes = [...registry.values()];
    const counts = window.h3Lifecycle.counts();
    return {
      current,
      mounted: modes.filter(m => m.mounted).map(m => m.id),
      registered: modes.length,
      scopes: counts.scopes,
      listeners: counts.listen,
      intervals: counts.interval,
      timeouts: counts.timeout,
      frames: counts.raf,
      urls: counts.url,
      cleanups: counts.other,
    };
  }

  /* the section the window opened on has never been "left", so enter it explicitly */
  function start() {
    if (current) return;
    const active = document.querySelector(".view.active");
    if (active) activate(active.id.replace(/^view-/, ""));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
  addEventListener("pagehide", disposeAll);

  window.h3Modes = { register, activate, ready, disposeAll, stats, current: () => current };
})();
