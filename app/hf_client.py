"""Hugging Face: LoRA search, README trigger extraction and link resolution.

Search never raises: no network, an HTTP 403 or invalid JSON produce an empty
result with a short Russian `error` (the UI shows «ничего не найдено» or asks
for a token). Repositories explicitly tagged as adult content are skipped.
"""

import asyncio
import json
import re
import urllib.parse

import aiohttp

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

HF_API = "https://huggingface.co/api/models"
ADULT_TAGS = {"nsfw", "not-safe-for-work", "explicit", "adult"}


class _SearchError(Exception):
    """Internal marker: the public search turns this into an empty result."""

    def __init__(self, message, needs_token=False):
        super().__init__(message)
        self.needs_token = needs_token


def _empty_result(message, needs_token=False):
    return {
        "items": [],
        "page": 1,
        "totalPages": 0,
        "totalItems": 0,
        "source": "huggingface",
        "error": message,
        "needsToken": bool(needs_token),
    }


def clean_text(text):
    """Plain text from a README/card fragment (tags and entities removed)."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    text = re.sub(r"&[a-zA-Z0-9#]+;", " ", text)
    return " ".join(text.split()).strip()


def extract_triggers_and_desc(readme_text):
    """Extract trigger words and a short description from a README.md."""
    if not readme_text:
        return [], ""
    triggers = []
    desc = ""

    # 1. YAML front matter: instance_prompt
    body = readme_text
    if readme_text.startswith("---"):
        parts = readme_text.split("---", 2)
        if len(parts) >= 3:
            front_matter = parts[1]
            body = parts[2]
            ip = re.search(r"(?i)instance_prompt:\s*>?-?\s*([^\n\r]+)", front_matter)
            if ip:
                val = ip.group(1).strip()
                if val and val != ">-":
                    triggers.append(val)

    # 2. "Trigger words:" lines in the body
    patterns = [
        r'(?i)\*\*Trigger\s*words?:\*\*\s*[`"\'*]*([^`"\'\n\r*#]{2,80})[`"\'*]*',
        r'(?i)(?:trigger(?: words?| keywords?)?|activation keywords?|trained words?)'
        r'[:\s*]+[`"\'*]*([^`"\'\n\r*#]{2,80})[`"\'*]*',
    ]
    for pattern in patterns:
        for found in re.findall(pattern, readme_text):
            cleaned = found.strip().strip("`'\"*")
            if cleaned and not cleaned.startswith(">-") and cleaned not in triggers and len(cleaned) > 1:
                triggers.append(cleaned)

    # 3. First meaningful paragraph as a fallback description.
    for paragraph in body.split("\n\n"):
        text = paragraph.strip()
        if not text or text.startswith(("#", "---", "<")):
            continue
        text = clean_text(re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text))
        if len(text) > 25:
            desc = text[:240]
            break

    return triggers[:10], desc


async def fetch_readme(session, mid, token=None):
    """Return `(model_id, README text)`; an empty text when it cannot be read."""
    url = f"https://huggingface.co/{mid}/raw/main/README.md"
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {str(token).strip()}"
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                return mid, await resp.text()
    except Exception:
        pass
    return mid, ""


async def _json_body(resp):
    try:
        return json.loads(await resp.text())
    except Exception:
        return None


async def _fetch_models(session, query, limit, token):
    q = (query or "").strip()
    search_query = f"{q} minimax h3" if q else "minimax h3 lora"
    try:
        limit = max(10, min(50, int(limit or 30)))
    except (TypeError, ValueError):
        limit = 30
    url = (
        f"{HF_API}?search={urllib.parse.quote_plus(search_query)}"
        f"&limit={limit}&cardData=true&full=true"
    )
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {str(token).strip()}"

    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status in (401, 403):
                raise _SearchError(
                    "Нужен токен Hugging Face: сервер отклонил запрос. "
                    "Добавьте токен в настройках каталога.",
                    needs_token=True,
                )
            if resp.status != 200:
                raise _SearchError(f"Hugging Face ответил ошибкой HTTP {resp.status}")
            data = await _json_body(resp)
    except _SearchError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        raise _SearchError("Нет связи с Hugging Face — проверьте интернет") from e

    if not isinstance(data, list):
        raise _SearchError("Hugging Face вернул некорректный ответ")
    return data


def _card(model):
    card = model.get("cardData")
    return card if isinstance(card, dict) else {}


def _tags(model):
    tags = model.get("tags")
    return [str(t) for t in tags] if isinstance(tags, list) else []


def _siblings(model):
    siblings = model.get("siblings")
    if not isinstance(siblings, list):
        return []
    return [str(s.get("rfilename") or "") for s in siblings if isinstance(s, dict)]


def _is_adult(tags, card):
    lowered = {t.lower() for t in tags}
    if lowered & ADULT_TAGS:
        return True
    if card.get("nsfw") is True:
        return True
    return str(card.get("content_rating") or "").lower() in ADULT_TAGS


def _safetensors_files(siblings):
    """LoRA weight files only: no sharded base models and no VAE/text-encoder folders."""
    return [
        f for f in siblings
        if f.lower().endswith(".safetensors")
        and not re.search(r"model-\d{5}-of-\d{5}", f)
        and not any(p in f.lower() for p in
                    ["text_encoder/", "transformer/", "audio_vae/", "video_vae/", "vae/"])
    ]


def _triggers_from_card(card):
    triggers = []
    if card.get("instance_prompt"):
        ip = str(card["instance_prompt"]).strip()
        if ip and ip != ">-":
            triggers.append(ip)
    tw = card.get("trigger_words")
    if isinstance(tw, list):
        triggers.extend(str(w).strip() for w in tw if str(w).strip())
    elif isinstance(tw, str) and tw.strip():
        triggers.append(tw.strip())
    return triggers


def _merge_unique(target, extra):
    for value in extra:
        if value and value not in target:
            target.append(value)


async def search_loras(session, query="", limit=30, token=None):
    """Search Hugging Face for MiniMax H3 LoRA models.

    Returns `{"items": [...], ...}`; on failure an empty list plus `error`.
    """
    try:
        models = await _fetch_models(session, query, limit, token)
    except _SearchError as e:
        return _empty_result(str(e), e.needs_token)
    except Exception:
        return _empty_result("Не удалось выполнить поиск на Hugging Face")

    # Gather READMEs for the top models to get exact trigger words.
    mids_to_fetch = [
        m.get("id") for m in models[:20]
        if isinstance(m, dict) and m.get("id") and not _card(m).get("instance_prompt")
    ]
    readmes = {}
    if mids_to_fetch:
        tasks = [fetch_readme(session, mid, token=token) for mid in mids_to_fetch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, tuple) and len(result) == 2:
                readmes[result[0]] = result[1]

    items = []
    for model in models:
        if not isinstance(model, dict):
            continue
        mid = str(model.get("id") or "")
        if not mid:
            continue

        tags = _tags(model)
        card = _card(model)
        if _is_adult(tags, card):
            continue

        siblings = _siblings(model)
        st_files = _safetensors_files(siblings)
        if not st_files:
            continue

        triggers = _triggers_from_card(card)
        readme_triggers, readme_desc = extract_triggers_and_desc(readmes.get(mid, ""))
        _merge_unique(triggers, readme_triggers)

        # Preview media: widget output first, then the first image/video in the repo.
        images = []
        for widget in card.get("widget") or []:
            if not isinstance(widget, dict):
                continue
            output = widget.get("output")
            out = output.get("url") if isinstance(output, dict) else None
            if out:
                images.append({"url": out})
                break
        if not images:
            media_files = [
                f for f in siblings
                if f.lower().endswith((".mp4", ".webm", ".jpg", ".png", ".jpeg", ".webp"))
                and not any(p in f.lower() for p in ["vae", "weights"])
            ]
            if media_files:
                images.append({
                    "url": f"https://huggingface.co/{mid}/resolve/main/{media_files[0]}",
                })

        parts = mid.split("/", 1)
        creator = parts[0] if len(parts) > 1 else ""
        name = (parts[1] if len(parts) > 1 else mid).replace("-", " ").replace("_", " ")

        desc = card.get("description") or card.get("summary") or readme_desc or ""
        if not desc:
            desc = f"LoRA-модель с Hugging Face от {creator or 'сообщества'}."
            if tags:
                desc += f" Теги: {', '.join(tags[:4])}"
        desc = clean_text(str(desc))[:260]

        versions = []
        for sf in st_files:
            clean_fn = sf.split("/")[-1]
            versions.append({
                "id": f"{mid}@{clean_fn}",
                "name": clean_fn.replace(".safetensors", ""),
                "baseModel": "MiniMax H3",
                "trainedWords": list(triggers),
                "filename": clean_fn,
                "sizeMB": 0,
                "downloadUrl": f"https://huggingface.co/{mid}/resolve/main/{sf}",
                "description": desc,
            })

        items.append({
            "id": mid,
            "name": name,
            "description": desc,
            "creator": creator,
            "images": images,
            "versions": versions,
            "civitaiUrl": f"https://huggingface.co/{mid}",
            "source": "huggingface",
            "downloads": model.get("downloads", 0),
            "likes": model.get("likes", 0),
        })

    return {
        "items": items,
        "page": 1,
        "totalPages": 1,
        "totalItems": len(items),
        "source": "huggingface",
    }


async def _get_model_json(session, repo_id, token=None):
    url = f"{HF_API}/{repo_id}?cardData=true&full=true"
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {str(token).strip()}"
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status in (401, 403):
                raise RuntimeError(
                    "Hugging Face требует токен для доступа к этому репозиторию. "
                    "Добавьте токен в настройках каталога."
                )
            if resp.status == 404:
                raise RuntimeError(f"Репозиторий {repo_id} не найден на Hugging Face")
            if resp.status != 200:
                raise RuntimeError(f"Hugging Face ответил ошибкой HTTP {resp.status}")
            data = await _json_body(resp)
    except RuntimeError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        raise RuntimeError("Нет связи с Hugging Face — проверьте интернет") from e
    if not isinstance(data, dict):
        raise RuntimeError("Hugging Face вернул некорректный ответ")
    return data


async def resolve_url_or_repo(session, text, token=None):
    """Resolve a Hugging Face link or repo id to a downloadable LoRA version."""
    text = str(text or "").strip()

    link = re.search(r"(?:huggingface\.co|hf\.co)/([^/]+/[^/?#]+)", text, re.IGNORECASE)
    if link:
        repo_and_rest = link.group(1)
    else:
        bare = re.match(r"^([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)", text)
        if not bare:
            raise ValueError(f"Не удалось распознать ссылку Hugging Face: {text}")
        repo_and_rest = bare.group(1)

    parts = repo_and_rest.split("/")
    if len(parts) < 2:
        raise ValueError(f"Не удалось распознать ссылку Hugging Face: {text}")
    author, repo_name = parts[0], parts[1]
    repo_id = f"{author}/{repo_name}"

    # A link may point at one exact file (`/blob/main/...` or `/resolve/main/...`).
    target_filename = None
    fn_match = re.search(r"/(?:blob|resolve)/[^/]+/([^?#]+\.safetensors)", text)
    if fn_match:
        target_filename = fn_match.group(1)

    model = await _get_model_json(session, repo_id, token=token)
    st_files = _safetensors_files(_siblings(model))
    if not st_files:
        raise RuntimeError(f"В репозитории {repo_id} не найдено .safetensors файлов LoRA.")

    if target_filename:
        if target_filename not in st_files:
            raise RuntimeError(f"В репозитории {repo_id} нет файла {target_filename}.")
        chosen_file = target_filename
    else:
        chosen_file = st_files[0]

    clean_fn = chosen_file.split("/")[-1]
    card = _card(model)
    triggers = _triggers_from_card(card)

    _, readme_text = await fetch_readme(session, repo_id, token=token)
    readme_triggers, readme_desc = extract_triggers_and_desc(readme_text)
    _merge_unique(triggers, readme_triggers)

    desc = (
        card.get("description")
        or card.get("summary")
        or readme_desc
        or f"LoRA {clean_fn} из Hugging Face ({repo_id})"
    )
    desc = clean_text(str(desc))[:260]

    return {
        "id": repo_id,
        "name": repo_name.replace("-", " ").replace("_", " "),
        "source": "huggingface",
        "version": {
            "id": f"{repo_id}@{clean_fn}",
            "name": clean_fn.replace(".safetensors", ""),
            "trainedWords": triggers,
            "filename": clean_fn,
            "sizeMB": 0,
            "downloadUrl": f"https://huggingface.co/{repo_id}/resolve/main/{chosen_file}",
            "description": desc,
        },
    }
