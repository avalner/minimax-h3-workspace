/* One place for HTTP between the window and its own server.
 *
 * The page had `postJSON` reading `j.error` and throwing `new Error(j.error)`: an agent-style
 * `{error: {code, message}}` became "[object Object]", a timeout did not exist at all, and every
 * call site repeated the same three lines. Here:
 *
 * - a wrong status is an `ApiError` with `status`, `code` and `hint` (the server's own words are
 *   used, so the window shows "Длительность должна быть от 3 до 25 секунд", not "409 Conflict");
 * - `timeout` aborts the request (AbortController) and says so, instead of hanging the button;
 * - a caller can pass its own `signal` (cancel button) — the timeout and that signal both abort.
 *
 * No automatic retry: a POST that starts a generation must never be repeated by the transport. The
 * idempotency key is the caller's decision, not a convenience of this module.
 */

export class ApiError extends Error {
  constructor(message, { status = 0, code = "", hint = "", path = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.hint = hint;
    this.path = path;
  }
}

/* The server speaks two dialects: the window's `{error: "текст"}` and the agent's
   `{error: {code, message, hint}}`. A plain text body is also possible (a 500 from aiohttp). */
function messageOf(payload, statusText, status) {
  if (typeof payload === "string" && payload.trim()) return payload.trim();
  const error = payload && payload.error;
  if (typeof error === "string" && error.trim()) return error.trim();
  if (error && typeof error === "object") return String(error.message || error.code || `Ошибка ${status}`);
  if (payload && payload.message) return String(payload.message);
  return String(statusText || `Ошибка ${status}`);
}

function detailsOf(payload) {
  const error = payload && payload.error;
  if (error && typeof error === "object") return { code: String(error.code || ""), hint: String(error.hint || "") };
  return { code: "", hint: "" };
}

export async function request(path, { method = "GET", body, headers = {}, timeout = 30000, signal } = {}) {
  const controller = new AbortController();
  const abortOuter = () => controller.abort(signal.reason);
  if (signal) {
    if (signal.aborted) controller.abort(signal.reason);
    else signal.addEventListener("abort", abortOuter, { once: true });
  }
  const timer = timeout ? setTimeout(() => controller.abort(new DOMException(`Нет ответа за ${Math.round(timeout / 1000)} с`, "TimeoutError")), timeout) : null;
  const init = { method, signal: controller.signal, headers: { ...headers } };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers["Content-Type"] = init.headers["Content-Type"] || "application/json";
  }
  let response, text;
  try {
    response = await fetch(path, init);
    text = await response.text();
  } catch (e) {
    if (controller.signal.reason && controller.signal.reason.name === "TimeoutError") throw new ApiError(String(controller.signal.reason.message), { path });
    if (signal && signal.aborted) throw new ApiError("Запрос отменён", { path });
    throw new ApiError("Нет связи с программой", { path });
  } finally {
    if (timer) clearTimeout(timer);
    if (signal) signal.removeEventListener("abort", abortOuter);
  }
  let payload = null;
  if (text) { try { payload = JSON.parse(text); } catch { payload = text; } }
  if (!response.ok) {
    const { code, hint } = detailsOf(payload);
    throw new ApiError(messageOf(payload, response.statusText, response.status),
                       { status: response.status, code, hint, path });
  }
  return payload;
}

export const getJSON = (path, options = {}) => request(path, options);
export const postJSON = (path, body, options = {}) => request(path, { ...options, method: "POST", body: body === undefined ? {} : body, timeout: options.timeout || 120000 });

/* The page is one classic script: a module cannot be imported by it. The module still owns the rules,
   and the page's own `postJSON` delegates here — one implementation, two doors. */
window.h3Api = { request, getJSON, postJSON, ApiError };
