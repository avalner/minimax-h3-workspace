/* The window's one event connection: open it, parse it, keep it open.
 *
 * What an event *means* is not this file's business: the page passes `onMessage`/`onBinary`, and
 * every rule about jobs, the queue and the Library stays there. This file only owns the transport:
 *
 * - one connection at a time (a second `connect()` closes the first);
 * - reconnect with a growing delay (0.5 s doubling to 15 s), reset after a successful open;
 * - the server's first message is the `hello` snapshot, so a reconnect always starts from state;
 * - a broken JSON line is dropped, not thrown: one bad message must not kill the window's feed.
 */

let current = null;

export function connect({ onMessage, onBinary = () => {}, onOpen = () => {}, onClose = () => {}, url } = {}) {
  if (current) current.close();
  const target = url || `ws://${location.host}/api/events`;
  let attempt = 0, socket = null, stopped = false;

  const open = () => {
    if (stopped) return;
    socket = new WebSocket(target);
    socket.binaryType = "blob";
    socket.onopen = () => { attempt = 0; onOpen(); };
    socket.onmessage = (e) => {
      if (typeof e.data !== "string") { onBinary(e.data); return; }
      let message;
      try { message = JSON.parse(e.data); } catch { return; }
      onMessage(message);
    };
    socket.onclose = () => {
      onClose();
      if (stopped) return;
      const delay = Math.min(15000, 500 * 2 ** Math.min(attempt++, 5));
      setTimeout(open, delay);
    };
    socket.onerror = () => {};   // a close event follows and the reconnect is scheduled there
  };

  open();
  current = {
    close() {
      stopped = true;
      try { if (socket) socket.close(); } catch { /* already gone */ }
      if (current === this) current = null;
    },
  };
  return current;
}

window.h3Events = { connect };
