/* One place for HTTP between the window and its own server.
 *
 * The page had `postJSON` reading `j.error` and throwing `new Error(j.error)`: an agent-style
 * `{error: {code, message}}` became "[object Object]", a timeout did not exist at all, and every
 * call site repeated the same three lines. Here:
 *
 * - a wrong status is an `ApiError` with `status`, `code` and `hint` (the server's own words are
 *   used, so the window shows "Длительность должна быть от 3 до 25 секунд", not "409 Conflict");
 * - `timeout` aborts the request (AbortController) and says so, instead of hanging the button;
 * - a caller can pass its own `signal` (cancel button) — the timeout and that signal both abort;
 * - `body` may be JSON (an object), FormData, a Blob, an ArrayBuffer or a string: files are sent as
 *   they are and no JSON header is invented for them;
 * - `raw` asks for the response itself (`"response"`, `"blob"`, `"text"`, `"arrayBuffer"`) when the
 *   caller needs bytes, headers or a status of its own.
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

function parse(text) {
  if (!text) return null;
  try { return JSON.parse(text); } catch { return text; }
}

function isRawBody(body) {
  return typeof body === "string" || body instanceof FormData || body instanceof Blob
    || body instanceof ArrayBuffer || ArrayBuffer.isView(body);
}

const RAW = new Set(["response", "blob", "text", "arrayBuffer"]);

export async function request(path, { method = "GET", body, headers = {}, timeout = 30000, signal, cache, raw } = {}) {
  if (raw !== undefined && !RAW.has(raw)) throw new Error(`api: unknown raw mode ${raw}`);
  const controller = new AbortController();
  const abortOuter = () => controller.abort(signal.reason);
  if (signal) {
    if (signal.aborted) controller.abort(signal.reason);
    else signal.addEventListener("abort", abortOuter, { once: true });
  }
  const timer = timeout ? setTimeout(() => controller.abort(new DOMException(`Нет ответа за ${Math.round(timeout / 1000)} с`, "TimeoutError")), timeout) : null;
  const init = { method, signal: controller.signal, headers: { ...headers } };
  if (cache) init.cache = cache;
  if (body !== undefined) {
    if (isRawBody(body)) init.body = body;
    else {
      init.body = JSON.stringify(body);
      init.headers["Content-Type"] = init.headers["Content-Type"] || "application/json";
    }
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (e) {
    if (controller.signal.reason && controller.signal.reason.name === "TimeoutError") throw new ApiError(String(controller.signal.reason.message), { path });
    if (signal && signal.aborted) throw new ApiError("Запрос отменён", { path });
    throw new ApiError("Нет связи с программой", { path });
  }
  try {
    if (!response.ok) {
      // the server's words are the message even for a byte response: read the body once, as text
      const payload = parse(await response.text().catch(() => ""));
      const { code, hint } = detailsOf(payload);
      throw new ApiError(messageOf(payload, response.statusText, response.status),
                         { status: response.status, code, hint, path });
    }
    if (raw === "response") return response;
    if (raw === "blob") return await response.blob();
    if (raw === "text") return await response.text();
    if (raw === "arrayBuffer") return await response.arrayBuffer();
    return parse(await response.text());
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (controller.signal.reason && controller.signal.reason.name === "TimeoutError") throw new ApiError(String(controller.signal.reason.message), { path });
    throw new ApiError("Связь оборвалась, пока читали ответ", { path });
  } finally {
    if (timer) clearTimeout(timer);
    if (signal) signal.removeEventListener("abort", abortOuter);
  }
}

export const getJSON = (path, options = {}) => request(path, options);
export const postJSON = (path, body, options = {}) => request(path, { ...options, method: "POST", body: body === undefined ? {} : body, timeout: options.timeout || 120000 });

/* The page is one classic script: a module cannot be imported by it. The module still owns the rules,
   and the page's own `postJSON` delegates here — one implementation, two doors. */
window.h3Api = { request, getJSON, postJSON, ApiError };
