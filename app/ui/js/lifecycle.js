/* A mode's resources in one place: listeners, timers, animation frames, object URLs, subscriptions.
 *
 * A section of the window can live for hours and the user can visit it a hundred times. Every
 * listener, interval, requestAnimationFrame and URL.createObjectURL must then have an owner that
 * gives it back, or the window grows until it is restarted. `scope(name)` is that owner: everything
 * the mode registers is removed by `dispose()`, which is idempotent and never throws.
 *
 * A classic script, loaded before the page and before the modes, so a mode can register itself while
 * the page is parsed.
 *
 * Contract:
 *   scope(name)              -> { listen, interval, timeout, raf, url, add, dispose, size, name }
 *   pauseMedia(root)         pause every <video>/<audio> under root (leaving a section)
 *   counts()                 { listen, interval, timeout, raf, url, other, scopes }
 *   disposeAll()             dispose every live scope (pagehide)
 */

(function () {
  "use strict";

  const live = new Set();

  function createScope(name) {
    const cleanups = [];
    let disposed = false;
    const counts = { listen: 0, interval: 0, timeout: 0, raf: 0, url: 0, other: 0 };

    const track = (kind, cleanup) => {
      cleanups.push(() => {
        try { cleanup(); } catch (e) { console.error(e); }
        counts[kind] -= 1;
      });
      counts[kind] += 1;
      return cleanup;
    };

    const scope = {
      name: name || "mode",
      get size() { return cleanups.length; },
      get disposed() { return disposed; },
      get counts() { return { ...counts }; },

      listen(target, type, fn, options) {
        if (!target) return fn;
        target.addEventListener(type, fn, options);
        track("listen", () => target.removeEventListener(type, fn, options));
        return fn;
      },

      interval(fn, ms) {
        const id = setInterval(fn, ms);
        track("interval", () => clearInterval(id));
        return id;
      },

      timeout(fn, ms) {
        const id = setTimeout(fn, ms);
        track("timeout", () => clearTimeout(id));
        return id;
      },

      /* one frame loop: `fn` is called every frame until the scope is disposed, and the next frame
         is scheduled after `fn` returns, so `fn` can stop the loop by throwing or by a flag */
      raf(fn) {
        let id = 0;
        const tick = now => { fn(now); id = requestAnimationFrame(tick); };
        id = requestAnimationFrame(tick);
        track("raf", () => cancelAnimationFrame(id));
        return id;
      },

      url(blob) {
        const u = URL.createObjectURL(blob);
        track("url", () => URL.revokeObjectURL(u));
        return u;
      },

      add(cleanup) {
        if (typeof cleanup !== "function") return cleanup;
        return track("other", cleanup);
      },

      dispose() {
        if (disposed) return;
        disposed = true;
        while (cleanups.length) {
          const cleanup = cleanups.pop();
          try { cleanup(); } catch (e) { console.error(e); }
        }
        live.delete(scope);
      },
    };
    live.add(scope);
    return scope;
  }

  function pauseMedia(root) {
    if (!root) return;
    root.querySelectorAll("video, audio").forEach(m => {
      try { m.pause(); } catch (e) { /* already gone */ }
    });
  }

  function counts() {
    const all = { listen: 0, interval: 0, timeout: 0, raf: 0, url: 0, other: 0, scopes: live.size };
    live.forEach(scope => {
      // the scope does not expose its counters: ask it to dispose? No. Keep a small registry copy.
      const c = scope.counts;
      if (c) Object.keys(c).forEach(k => { all[k] += c[k]; });
    });
    return all;
  }

  window.h3Lifecycle = {
    scope: createScope,
    pauseMedia,
    counts,
    get scopes() { return [...live]; },
    disposeAll() { [...live].forEach(scope => scope.dispose()); },
  };
})();
