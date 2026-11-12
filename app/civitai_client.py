"""Civitai: LoRA search, link resolution and resumable downloads.

The studio only asks Civitai for safe content: the `nsfw` query parameter is
always sent as false, whatever the compatibility argument says. Search never
raises: no network, an HTTP 403 or invalid JSON produce an empty result with a
short Russian `error`, so the UI can show «ничего не найдено» or ask for a token.
"""

import asyncio
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

import aiohttp

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

MIRRORS = {"com": "civitai.com", "red": "civitai.red"}

# The API token is a secret: it is only attached to these hosts (and their subdomains).
TOKEN_HOSTS = ("civitai.com", "civitai.red", "huggingface.co")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
REDIRECT_CODES = (301, 302, 303, 307, 308)

# State of the single running download, read by the UI through the server.
CURRENT_DOWNLOAD = {
    "active": False,
    "file": "",
    "name": "",
    "done": 0,
    "total": 0,
    "percent": 0,
    "speed": "",
    "status": "idle",  # "idle", "downloading", "done", "error"
    "error": None,
    "task": None,
}


class _SearchError(Exception):
    """Internal marker: the public search turns this into an empty result."""

    def __init__(self, message, needs_token=False):
        super().__init__(message)
        self.needs_token = needs_token


def _mirror_host(mirror):
    return MIRRORS["red" if str(mirror or "").lower() == "red" else "com"]


def clean_html(raw_html):
    """Plain text from Civitai's HTML description (tags and entities removed)."""
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(raw_html))
    text = re.sub(r"&[a-zA-Z0-9#]+;", " ", text)
    return " ".join(text.split()).strip()


def _int_or_zero(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _empty_result(mirror, message, needs_token=False, page=1):
    return {
        "items": [],
        "page": page,
        "totalPages": 0,
        "totalItems": 0,
        "mirror": mirror,
        "error": message,
        "needsToken": bool(needs_token),
    }


async def _json_body(resp):
    """Parse a response as JSON; None when the body is not valid JSON."""
    try:
        raw = await resp.text()
        return json.loads(raw)
    except Exception:
        return None


def _trained_words(value):
    if isinstance(value, str):
        return [w.strip() for w in value.split(",") if w.strip()]
    if isinstance(value, list):
        return [str(w).strip() for w in value if str(w).strip()]
    return []


async def _search_once(session, query, page, limit, mirror, cursor, token):
    domain = _mirror_host(mirror)
    try:
        limit = min(50, max(5, int(limit or 20)))
    except (TypeError, ValueError):
        limit = 20

    params = {
        "types": "LORA",
        "baseModels": "MiniMax H3",
        # Safe content only: this is a general creative studio.
        "nsfw": "false",
        "limit": str(limit),
    }
    q = (query or "").strip()
    # Civitai does not allow `page` together with `query`; it uses a cursor there.
    if q:
        params["query"] = q
        if cursor:
            params["cursor"] = str(cursor)
    elif cursor:
        params["cursor"] = str(cursor)
    elif page:
        params["page"] = str(max(1, _int_or_zero(page) or 1))

    if token:
        params["token"] = str(token).strip()

    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {str(token).strip()}"

    url = f"https://{domain}/api/v1/models"
    try:
        async with session.get(
            url, params=params, headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                if resp.status in (401, 403):
                    raise _SearchError(
                        "Нужен API-токен Civitai: сервер отклонил запрос. "
                        "Добавьте токен в настройках каталога.",
                        needs_token=True,
                    )
                raise _SearchError(f"Civitai ответил ошибкой HTTP {resp.status}")
            data = await _json_body(resp)
    except _SearchError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        raise _SearchError("Нет связи с Civitai — проверьте интернет") from e

    if not isinstance(data, dict):
        raise _SearchError("Civitai вернул некорректный ответ")

    items = []
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raw_items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if item.get("nsfw") is True:
            continue

        mid = item.get("id")
        name = item.get("name") or ""
        desc = clean_html(item.get("description") or "")
        creator_obj = item.get("creator")
        creator = creator_obj.get("username", "") if isinstance(creator_obj, dict) else ""
        model_versions = item.get("modelVersions")
        if not isinstance(model_versions, list):
            model_versions = []

        # A few safe previews from the first version that has any.
        images = []
        for v in model_versions:
            if not isinstance(v, dict):
                continue
            for img in v.get("images") or []:
                if not isinstance(img, dict):
                    continue
                # Skip anything the API still marks as not safe.
                if img.get("nsfw") is True or _int_or_zero(img.get("nsfwLevel")) > 1:
                    continue
                img_url = img.get("url")
                if img_url:
                    images.append({
                        "url": img_url,
                        "width": img.get("width"),
                        "height": img.get("height"),
                    })
            if images:
                break

        versions = []
        for v in model_versions:
            if not isinstance(v, dict):
                continue
            base_model = str(v.get("baseModel") or "")
            if "minimax" not in base_model.lower():
                continue

            files = [f for f in (v.get("files") or []) if isinstance(f, dict)]
            model_file = next(
                (f for f in files
                 if f.get("type") == "Model" and str(f.get("name") or "").lower().endswith(".safetensors")),
                None,
            )
            if not model_file:
                model_file = next(
                    (f for f in files if str(f.get("name") or "").lower().endswith(".safetensors")),
                    files[0] if files else None,
                )
            if not model_file:
                continue

            try:
                size_mb = round(float(model_file.get("sizeKB") or 0) / 1024, 1)
            except (TypeError, ValueError):
                size_mb = 0.0

            versions.append({
                "id": v.get("id"),
                "name": v.get("name") or "Default",
                "baseModel": base_model,
                "trainedWords": _trained_words(v.get("trainedWords")),
                "filename": model_file.get("name") or f"lora_{v.get('id')}.safetensors",
                "sizeMB": size_mb,
                "downloadUrl": model_file.get("downloadUrl")
                or f"https://{domain}/api/download/models/{v.get('id')}",
                "description": clean_html(v.get("description") or ""),
            })

        if versions:
            items.append({
                "id": mid,
                "name": name,
                "description": desc,
                "creator": creator,
                "images": images[:4],
                "versions": versions,
                "civitaiUrl": f"https://{domain}/models/{mid}",
                "source": "civitai",
            })

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return {
        "items": items,
        "page": metadata.get("currentPage", page),
        "totalPages": metadata.get("totalPages", 1),
        "totalItems": metadata.get("totalItems", len(items)),
        "mirror": mirror,
    }


async def search_loras(session, query="", page=1, limit=20, mirror="com", nsfw=False,
                       cursor=None, token=None):
    """Search Civitai for MiniMax H3 LoRAs.

    `nsfw` is kept only for call compatibility: safe content is always requested.
    Returns `{"items": [...], ...}`; on any failure an empty list plus `error`.
    """
    del nsfw  # always request safe models, never let a caller switch it on
    want = "red" if str(mirror or "").lower() == "red" else "com"
    try:
        return await _search_once(session, query, page, limit, want, cursor, token)
    except _SearchError as e:
        first_error = e
    except Exception:
        first_error = _SearchError("Не удалось выполнить поиск на Civitai")

    if want == "red":
        # civitai.red is a mirror: fall back to the main site when it is down.
        try:
            return await _search_once(session, query, page, limit, "com", cursor, token)
        except _SearchError as e:
            return _empty_result("com", str(e), e.needs_token, page)
        except Exception:
            return _empty_result("com", "Не удалось выполнить поиск на Civitai", page=page)

    return _empty_result(want, str(first_error), first_error.needs_token, page)


async def _get_json(session, url, timeout=15):
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status in (401, 403):
                raise RuntimeError(
                    "Civitai требует API-токен для доступа к этой модели. "
                    "Добавьте токен в настройках каталога."
                )
            if resp.status == 404:
                raise RuntimeError("Модель не найдена на Civitai (HTTP 404)")
            if resp.status != 200:
                raise RuntimeError(f"Civitai ответил ошибкой HTTP {resp.status}")
            data = await _json_body(resp)
    except RuntimeError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        raise RuntimeError("Нет связи с Civitai — проверьте интернет") from e
    if not isinstance(data, dict):
        raise RuntimeError("Civitai вернул некорректный ответ")
    return data


def _version_payload(domain, version, fallback_id):
    """One download version in the shape the UI expects."""
    files = [f for f in (version.get("files") or []) if isinstance(f, dict)]
    model_file = next(
        (f for f in files if str(f.get("name") or "").lower().endswith(".safetensors")),
        files[0] if files else {},
    )
    try:
        size_mb = round(float(model_file.get("sizeKB") or 0) / 1024, 1)
    except (TypeError, ValueError):
        size_mb = 0.0
    return {
        "id": version.get("id"),
        "name": version.get("name") or "Default",
        "trainedWords": _trained_words(version.get("trainedWords")),
        "filename": model_file.get("name") or f"lora_{fallback_id}.safetensors",
        "sizeMB": size_mb,
        "downloadUrl": model_file.get("downloadUrl")
        or f"https://{domain}/api/download/models/{fallback_id}",
        "description": clean_html(version.get("description") or ""),
    }


async def resolve_url_or_id(session, text, mirror="com"):
    """Resolve a Civitai model link, model id or version id into model/version info."""
    text = str(text or "").strip()
    domain = _mirror_host(mirror)
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}

    # Direct link to a version: ...?modelVersionId=123
    v_match = re.search(r"modelVersionId=(\d+)", text)
    if v_match:
        vid = v_match.group(1)
        v = await _get_json(session, f"https://{domain}/api/v1/model-versions/{vid}")
        return {
            "id": v.get("modelId"),
            "name": v.get("name"),
            "source": "civitai",
            "version": _version_payload(domain, v, vid),
        }

    # Model page link (.../models/123) or a bare numeric id.
    m_match = re.search(r"models/(\d+)", text) or re.match(r"^(\d+)$", text)
    if m_match:
        mid = m_match.group(1)
        model = await _get_json(session, f"https://{domain}/api/v1/models/{mid}")
        versions = [v for v in (model.get("modelVersions") or []) if isinstance(v, dict)]
        chosen = next(
            (v for v in versions if "minimax" in str(v.get("baseModel") or "").lower()),
            versions[0] if versions else None,
        )
        if not chosen:
            raise RuntimeError("У этой модели на Civitai нет файлов для скачивания")
        return {
            "id": model.get("id"),
            "name": model.get("name"),
            "source": "civitai",
            "version": _version_payload(domain, chosen, chosen.get("id")),
        }

    raise ValueError(f"Не удалось распознать ссылку или ID Civitai: {text}")


# ---------------------------------------------------------------- downloading

def _host_of(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _host_matches(host, allowed):
    return bool(host) and any(host == h or host.endswith("." + h) for h in allowed)


def _check_download_url(url):
    """Only https, with a loopback exception for local tests and mirrors."""
    try:
        parsed = urllib.parse.urlsplit(url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
    except ValueError:
        raise RuntimeError("Некорректная ссылка для скачивания")
    if scheme not in ("http", "https"):
        raise RuntimeError("Скачивание разрешено только по https")
    if scheme == "http" and host not in LOOPBACK_HOSTS:
        raise RuntimeError("Скачивание разрешено только по https (исключение — 127.0.0.1)")
    if not host:
        raise RuntimeError("В ссылке для скачивания нет адреса сервера")


def _with_token_query(url, token):
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    if not token or "token=" in parts.query:
        return url
    query = (parts.query + "&" if parts.query else "") + "token=" + urllib.parse.quote(token, safe="")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


async def _open_download(session, url, token, have, max_hops=5):
    """GET the file without aiohttp's redirects: every hop's URL and token host are checked."""
    current = url
    first_hop = True
    for _ in range(max_hops + 1):
        _check_download_url(current)
        headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"}
        if token and _host_matches(_host_of(current), TOKEN_HOSTS):
            headers["Authorization"] = f"Bearer {token}"
            # Civitai's download endpoint reads the token from the query as well.
            # Only on the first request: redirects go to signed CDN URLs, and an
            # extra query parameter could invalidate their signature.
            if first_hop and _host_matches(_host_of(current), ("civitai.com", "civitai.red")):
                current = _with_token_query(current, token)
        first_hop = False
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            resp = await session.get(
                current, headers=headers,
                timeout=aiohttp.ClientTimeout(total=None, sock_read=120),
                allow_redirects=False,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            raise RuntimeError("Нет связи с сервером загрузки — проверьте интернет") from e

        if resp.status in REDIRECT_CODES:
            location = resp.headers.get("Location")
            resp.release()
            if not location:
                raise RuntimeError(
                    f"Сервер загрузки ответил перенаправлением без адреса (HTTP {resp.status})"
                )
            current = urllib.parse.urljoin(current, location)
            continue
        return resp

    raise RuntimeError("Слишком много перенаправлений при скачивании")


def _content_range_total(value):
    """Total file size from `Content-Range: bytes a-b/total` (or `bytes */total`)."""
    m = re.search(r"/(\d+)\s*$", str(value or ""))
    return int(m.group(1)) if m else 0


def _content_length(resp):
    try:
        return max(0, int(resp.headers.get("Content-Length") or 0))
    except (TypeError, ValueError):
        return 0


async def download_file_stream(session, url, dest_path, token=None):
    """Download a LoRA file, resuming a previous `.part`, and track progress.

    The download is https-only (127.0.0.1 is allowed for local mirrors), the
    token goes only to Civitai/Hugging Face hosts, and `dest_path` is replaced
    atomically only after the size matches the server's total.
    """
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    token = str(token or "").strip()

    CURRENT_DOWNLOAD.update({
        "active": True,
        "status": "downloading",
        "error": None,
        "file": dest.name,
        "done": 0,
        "total": 0,
        "percent": 0,
        "speed": "",
    })

    have = part.stat().st_size if part.exists() else 0
    total = 0
    start_time = time.time()
    downloaded_this_session = 0

    try:
        if not str(url or "").strip():
            raise RuntimeError("Пустой URL")

        resp = await _open_download(session, url, token, have)
        try:
            if resp.status in (401, 403):
                raise RuntimeError(
                    "Скачивание требует авторизацию (API-токен). "
                    "Введите токен в настройках каталога LoRA."
                )
            final_url = urllib.parse.urlsplit(str(resp.url))
            final_host = (final_url.hostname or "").lower()
            if final_host == "auth.civitai.com" or final_url.path.rstrip("/").endswith("/login"):
                raise RuntimeError(
                    "Civitai перенаправил на вход в аккаунт. Нужен бесплатный API-токен "
                    "Civitai (Settings -> API Keys)."
                )
            if resp.status not in (200, 206, 416):
                raise RuntimeError(f"HTTP ошибка при скачивании: {resp.status}")
            content_type = str(resp.headers.get("Content-Type") or "").lower()
            if resp.status != 416 and content_type.startswith("text/html"):
                raise RuntimeError(
                    "Сервер вернул страницу вместо файла. Скорее всего, нужен API-токен "
                    "или ссылка на скачивание устарела."
                )

            if resp.status == 200:
                # The server ignored Range and sends the whole file: start over.
                have = 0
                total = _content_length(resp)
            elif resp.status == 206:
                total = _content_range_total(resp.headers.get("Content-Range"))
                if total <= 0:
                    content_length = _content_length(resp)
                    total = have + content_length if content_length > 0 else 0
            else:
                # 416: the server has nothing to send from this offset. Treat the
                # file as complete only when the `.part` really matches its total.
                total = _content_range_total(resp.headers.get("Content-Range"))
                if not part.exists() or total <= 0 or part.stat().st_size != total:
                    raise RuntimeError(
                        "Файл недокачан: локальный фрагмент не совпадает с размером "
                        "на сервере. Удалите его и скачайте заново."
                    )
                have = total

            CURRENT_DOWNLOAD["total"] = total
            CURRENT_DOWNLOAD["done"] = have
            if total > 0:
                CURRENT_DOWNLOAD["percent"] = min(100, round((have / total) * 100, 1))

            if resp.status != 416:
                mode = "ab" if (have and resp.status == 206) else "wb"
                with open(part, mode) as f:
                    async for chunk in resp.content.iter_chunked(256 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        have += len(chunk)
                        downloaded_this_session += len(chunk)
                        CURRENT_DOWNLOAD["done"] = have
                        if total > 0:
                            CURRENT_DOWNLOAD["percent"] = min(100, round((have / total) * 100, 1))
                        elapsed = time.time() - start_time
                        if elapsed > 0.5:
                            speed_mb = (downloaded_this_session / (1024 * 1024)) / elapsed
                            CURRENT_DOWNLOAD["speed"] = f"{speed_mb:.1f} МБ/с"
        finally:
            resp.release()

        # Finalize: verify against the server's total before the atomic replace.
        if not part.exists():
            raise RuntimeError("Файл не скачан")
        size = part.stat().st_size
        if total > 0 and size != total:
            raise RuntimeError(
                f"Загрузка неполная: {size} из {total} байт. "
                "Повторите скачивание — докачка продолжится."
            )
        os.replace(part, dest)

        CURRENT_DOWNLOAD["status"] = "done"
        CURRENT_DOWNLOAD["percent"] = 100
        CURRENT_DOWNLOAD["speed"] = "Завершено"
    except Exception as e:
        CURRENT_DOWNLOAD["status"] = "error"
        CURRENT_DOWNLOAD["error"] = str(e)
        raise
    finally:
        CURRENT_DOWNLOAD["active"] = False
