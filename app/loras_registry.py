"""Knowledge base and metadata helper for MiniMax H3 LoRAs.

Provides human-readable names, categories, descriptions and trigger words for
known LoRA families, reads the .safetensors header, merges user-saved metadata,
analyses conflicts between simultaneously active LoRAs and formats LoRA
guidance for the prompt-writing agent.

The built-in catalogue is deliberately neutral: the studio is a general
creative tool, so it only knows realism, lighting, camera, film and style
families. Any other file falls back to its own header or to its file name.
"""

import json
import re
from pathlib import Path

# Built-in knowledge base: generic families, not specific third-party uploads.
KNOWN_LORAS = [
    {
        "pattern": r"realism|realistic[_\- ]?skin|skin[_\- ]?detail|h3[-_]?realism",
        "name": "Реализм и детализация кожи",
        "category": "Реализм",
        "desc": "Естественная текстура кожи и мелкие детали лица вместо «пластиковой» картинки. "
                "Подходит для портретов, крупных планов и документальной подачи.",
        "triggers": ["natural skin texture", "detailed face", "realistic photo"],
        "recommended_weight": 0.6,
    },
    {
        "pattern": r"cinematic|film[_\- ]?light|movie[_\- ]?still|anamorphic",
        "name": "Кинематографичный свет",
        "category": "Свет и атмосфера",
        "desc": "Мягкий контровой свет, объёмные лучи и благородная контрастность кадра. "
                "Делает сцену похожей на кадр из фильма.",
        "triggers": ["cinematic lighting", "volumetric light", "soft rim light"],
        "recommended_weight": 0.7,
    },
    {
        "pattern": r"camera[_\- ]?motion|motion[_\- ]?booster|speed[_\- ]?slider|camera[_\- ]?move|dolly",
        "name": "Движение камеры",
        "category": "Камера и движение",
        "desc": "Плавные проезды, наклоны и смены ракурса. Управляет динамикой камеры, "
                "чтобы сцена не выглядела статичной.",
        "triggers": ["smooth camera movement", "dynamic camera angle", "slow dolly in"],
        "recommended_weight": 0.7,
    },
    {
        "pattern": r"film[_\- ]?grain|film[_\- ]?look|35mm|analog|halation|kodak",
        "name": "Плёночный вид",
        "category": "Стиль изображения",
        "desc": "Зерно, мягкие световые ореолы и аналоговая цветокоррекция. "
                "Придаёт видео тёплый плёночный характер.",
        "triggers": ["35mm film", "film grain", "soft halation"],
        "recommended_weight": 0.65,
    },
    {
        "pattern": r"(?:^|[_\-\s])style(?:$|[_\-\s.])|illustration|painterly|art[_\- ]?style",
        "name": "Художественный стиль",
        "category": "Стиль изображения",
        "desc": "Рисованная манера, цифровая живопись и чистые контуры. "
                "Переводит сцену в иллюстративный стиль.",
        "triggers": ["illustration", "painterly", "clean line art"],
        "recommended_weight": 0.65,
    },
]

DEFAULT_CATEGORY = "Пользовательская LoRA"
HEADER_LIMIT = 20 * 1024 * 1024  # a sane safetensors header is a few KB to a few MB

# Conflict thresholds (sum of positive strengths of active LoRAs).
MAX_ACTIVE_LORAS = 4
OVERCOOKED_TOTAL = 1.4
OVERCOOKED_DANGER = 2.5


def read_safetensors_metadata(filepath):
    """Return `__metadata__` from a .safetensors header, or `{}` for a broken file."""
    try:
        path = Path(filepath)
        if not path.is_file():
            return {}
        with open(path, "rb") as f:
            size_bytes = f.read(8)
            if len(size_bytes) != 8:
                return {}
            header_size = int.from_bytes(size_bytes, "little")
            if header_size <= 0 or header_size > HEADER_LIMIT:
                return {}
            header_raw = f.read(header_size)
            if len(header_raw) != header_size:
                return {}
            data = json.loads(header_raw.decode("utf-8", errors="ignore"))
            if not isinstance(data, dict):
                return {}
            meta = data.get("__metadata__")
            return meta if isinstance(meta, dict) else {}
    except Exception:
        # Any malformed/truncated file is simply "no metadata", never an error.
        return {}


def format_clean_name(raw):
    """Turn `my_lora_name_v1.safetensors` into a short human title."""
    name = str(raw or "")
    stem = Path(name).stem
    text = re.sub(r"[_\-]+", " ", stem)
    text = re.sub(r"\b(v\d+(?:\.\d+)?|safetensors|minimax|h3)\b", "", text, flags=re.IGNORECASE)
    text = " ".join(text.split()).strip()
    if not text:
        return stem or name
    words = []
    for word in text.split():
        if word.lower() == "lora":
            words.append("LoRA")
        elif word.isupper() and len(word) > 1:
            words.append(word)
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _custom_entry(custom_meta, *keys):
    """Find a user override by exact key or by a slash-normalised path."""
    if not isinstance(custom_meta, dict):
        return {}
    for key in keys:
        value = custom_meta.get(key)
        if isinstance(value, dict):
            return value
    wanted = {str(k).replace("/", "\\").lower() for k in keys if k}
    for key, value in custom_meta.items():
        if isinstance(value, dict) and str(key).replace("/", "\\").lower() in wanted:
            return value
    return {}


def _embedded_metadata(full_file_path):
    """Triggers and description stored inside the .safetensors header."""
    triggers = []
    desc = ""
    if not full_file_path:
        return triggers, desc
    meta = read_safetensors_metadata(full_file_path)
    if not meta:
        return triggers, desc

    trained = (
        meta.get("modelspec.trained_words")
        or meta.get("trainedWords")
        or meta.get("ss_trained_words")
    )
    if isinstance(trained, str) and trained.strip().startswith("["):
        # Some trainers store the list as a JSON string.
        try:
            as_list = json.loads(trained)
        except ValueError:
            as_list = None
        triggers = _clean_triggers(as_list) if isinstance(as_list, list) else _clean_triggers(trained)
    elif trained is not None:
        triggers = _clean_triggers(trained)

    # kohya stores tag frequencies as a JSON string; take the most frequent tags.
    if not triggers and meta.get("ss_tag_frequency"):
        try:
            freq = meta["ss_tag_frequency"]
            if isinstance(freq, str):
                freq = json.loads(freq)
            if isinstance(freq, dict):
                for tags in freq.values():
                    if not isinstance(tags, dict):
                        continue
                    for tag in sorted(tags, key=lambda k: tags[k], reverse=True)[:10]:
                        if tag not in triggers:
                            triggers.append(str(tag))
        except Exception:
            pass

    desc = str(meta.get("modelspec.description") or meta.get("description") or "")
    return triggers, desc


def _clean_triggers(value):
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, list):
        return [str(t).strip() for t in value if t is not None and str(t).strip()]
    return []


def get_lora_metadata(file_path_or_name, full_file_path=None, custom_meta=None):
    """Rich metadata for any LoRA file: overrides, built-ins, header, clean name.

    Priority: user overrides -> built-in family -> .safetensors header -> file name.
    """
    clean_name = Path(str(file_path_or_name or "")).name
    lower_name = clean_name.lower()
    overrides = _custom_entry(custom_meta, file_path_or_name, clean_name)

    matched = None
    for entry in KNOWN_LORAS:
        if re.search(entry["pattern"], lower_name):
            matched = entry
            break

    header_path = full_file_path
    if not header_path:
        try:
            if Path(str(file_path_or_name)).is_file():
                header_path = file_path_or_name
        except OSError:
            header_path = None
    embedded_triggers, embedded_desc = _embedded_metadata(header_path)

    title = overrides.get("name") or (matched["name"] if matched else format_clean_name(clean_name))
    category = overrides.get("category") or (matched["category"] if matched else DEFAULT_CATEGORY)
    if overrides.get("desc"):
        desc = str(overrides["desc"])
    elif matched:
        desc = matched["desc"]
    elif embedded_desc:
        desc = embedded_desc[:250]
    else:
        desc = "Пользовательская LoRA для MiniMax H3."

    triggers = overrides.get("triggers")
    if triggers is None:
        triggers = list(matched["triggers"]) if matched else list(embedded_triggers)
    else:
        triggers = _clean_triggers(triggers)

    if overrides.get("recommended_weight") is not None:
        recommended_weight = _as_float(overrides.get("recommended_weight"), 0.8)
    elif matched:
        recommended_weight = float(matched["recommended_weight"])
    else:
        recommended_weight = 0.8

    return {
        "file": str(file_path_or_name),
        "filename": clean_name,
        "name": title,
        "category": category,
        "desc": desc,
        "triggers": triggers,
        "recommended_weight": recommended_weight,
    }


def _parse_active(active_items, custom_meta):
    """Normalise active LoRA entries into the records used by the conflict rules."""
    parsed = []
    total_strength = 0.0
    for item in active_items or []:
        if isinstance(item, dict):
            if not item.get("on", True):
                continue
            file_name = str(item.get("file") or "").strip()
            strength = _as_float(item.get("strength"), 0.8)
        else:
            file_name = str(item or "").strip()
            strength = 0.8
        if not file_name:
            continue

        meta = get_lora_metadata(file_name, custom_meta=custom_meta)
        parsed.append({
            "file": file_name,
            "filename": Path(file_name).name,
            "lower": Path(file_name).name.lower(),
            "name": meta["name"],
            "category": meta.get("category") or DEFAULT_CATEGORY,
            "triggers": meta.get("triggers") or [],
            "recommended_weight": _as_float(meta.get("recommended_weight"), 0.8),
            "strength": strength,
        })
        if strength > 0:
            total_strength += strength
    return parsed, total_strength


def _names_text(items, limit=3):
    names = [f"«{p['name']}»" for p in items[:limit]]
    if len(items) > limit:
        names.append("…")
    return ", ".join(names)


def _unique_by_file(items):
    seen = set()
    out = []
    for item in items:
        if item["lower"] in seen:
            continue
        seen.add(item["lower"])
        out.append(item)
    return out


def _components(parsed, edges):
    """Connected groups of LoRAs that share trigger words."""
    adjacency = {p["file"]: set() for p in parsed}
    for left, right, _ in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)

    by_file = {p["file"]: p for p in parsed}
    seen = set()
    groups = []
    for start in adjacency:
        if start in seen or not adjacency[start]:
            continue
        stack = [start]
        seen.add(start)
        group = []
        while stack:
            current = stack.pop()
            group.append(by_file[current])
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        if len(group) >= 2:
            groups.append(group)
    return groups


def analyze_lora_conflicts(active_items, custom_meta=None, builtins=None):
    """Check active LoRAs for practical problems, strongest first.

    The rules are deliberately generic (no titles of specific uploads):
      * the same file is listed twice;
      * too many active LoRAs;
      * the total weight is too high ("overcooked");
      * two LoRAs share a category or trigger words;
      * a weight is far above the recommended one.

    Every item has the shape
    `{"id", "level" ("danger"|"warning"|"caution"), "title", "desc",
      "culprits", "action": {"type", "target", "label"}}`
    where action types match what the UI already knows: disable / keep_one /
    scale_down.
    """
    conflicts = []
    if not active_items:
        return conflicts

    parsed, total_strength = _parse_active(active_items, custom_meta)

    # Built-in switches may add weight too ({"realism": {"on": True, "v": 0.5}}).
    # "speed" is a slider, not a LoRA weight, so it does not count.
    if isinstance(builtins, dict):
        for key, value in builtins.items():
            if not isinstance(value, dict) or not value.get("on"):
                continue
            if key == "speed":
                continue
            weight = _as_float(value.get("v"), 0.5)
            if weight > 0:
                total_strength += weight

    if not parsed:
        return conflicts

    # 1. The same file twice: its weights are applied twice.
    by_name = {}
    for item in parsed:
        by_name.setdefault(item["lower"], []).append(item)
    for group in by_name.values():
        if len(group) < 2:
            continue
        culprits = [p["file"] for p in group]
        conflicts.append({
            "id": "duplicate_file",
            "level": "danger",
            "title": f"Один файл включён дважды ({group[0]['name']})",
            "desc": (
                f"Файл «{group[0]['filename']}» указан в списке {len(group)} раза. "
                "Его веса применяются дважды, поэтому картинка перегружается: "
                "кожа становится восковой, а движение — резким. Оставьте один экземпляр."
            ),
            "culprits": culprits,
            "action": {
                "type": "keep_one",
                "target": group[0]["file"],
                "culprits": culprits,
                "label": f"Оставить один экземпляр {group[0]['name']}",
            },
        })

    # 2. Too many active LoRAs: each one pulls the style in its own direction.
    if len(parsed) > MAX_ACTIVE_LORAS:
        weakest = min(parsed, key=lambda p: p["strength"])
        conflicts.append({
            "id": "too_many_loras",
            "level": "warning",
            "title": f"Слишком много активных LoRA ({len(parsed)})",
            "desc": (
                f"Одновременно включено {len(parsed)} LoRA при разумном пределе {MAX_ACTIVE_LORAS}. "
                "Чем больше моделей, тем сильнее они спорят за стиль и движение: "
                "лица «плывут», свет становится грязным, а камера — дёрганой. "
                "Отключите лишние, оставив 2–3 главные."
            ),
            "culprits": [p["file"] for p in parsed],
            "action": {
                "type": "disable",
                "target": weakest["file"],
                "label": f"Отключить {weakest['name']}",
            },
        })

    # 3. Overcooked total weight.
    if total_strength > OVERCOOKED_TOTAL:
        level = "danger" if total_strength > OVERCOOKED_DANGER else "warning"
        conflicts.append({
            "id": "overcooking",
            "level": level,
            "title": f"Перегруз суммарного веса LoRA ({total_strength:.2f})",
            "desc": (
                f"Суммарная сила активных LoRA — {total_strength:.2f} при комфортной норме "
                f"до {OVERCOOKED_TOTAL:.1f}. Перегруз разрушает картинку: кожа становится "
                "пластиковой, мелкие детали «плавятся», появляются тёмные артефакты. "
                "Снизьте веса до 0.5–0.6."
            ),
            "culprits": [p["file"] for p in parsed],
            "action": {
                "type": "scale_down",
                "target": "0.6",
                "label": "Снизить силу активных LoRA до 0.60",
            },
        })

    # 4. Same category: two models of one family usually do the same thing.
    by_category = {}
    for item in parsed:
        category = (item.get("category") or "").strip()
        if not category or category == DEFAULT_CATEGORY:
            continue
        by_category.setdefault(category, []).append(item)
    for category, group in sorted(by_category.items()):
        unique = _unique_by_file(group)
        if len(unique) < 2:
            continue
        culprits = [p["file"] for p in unique]
        conflicts.append({
            "id": "same_category",
            "level": "caution",
            "title": f"Похожее назначение: {category} ({len(unique)})",
            "desc": (
                f"Активны LoRA одной категории ({_names_text(unique)}). "
                "Они решают одну задачу и суммируют эффект, поэтому стиль легко "
                "становится «пережаренным». Для чистой картинки достаточно одной."
            ),
            "culprits": culprits,
            "action": {
                "type": "keep_one",
                "target": unique[0]["file"],
                "culprits": culprits,
                "label": f"Оставить только {unique[0]['name']}",
            },
        })

    # 5. Trigger-word overlap between different categories.
    edges = []
    for i, left in enumerate(parsed):
        for right in parsed[i + 1:]:
            if left["lower"] == right["lower"]:
                continue
            if left.get("category") == right.get("category") and left.get("category") != DEFAULT_CATEGORY:
                continue  # already reported as same category
            left_triggers = {str(t).strip().lower() for t in left["triggers"] if str(t).strip()}
            right_triggers = {str(t).strip().lower() for t in right["triggers"] if str(t).strip()}
            shared = left_triggers & right_triggers
            if shared:
                edges.append((left["file"], right["file"], sorted(shared)))

    for group in _components(parsed, edges):
        culprits = [p["file"] for p in group]
        group_files = {p["file"] for p in group}
        shared_words = []
        for left, right, words in edges:
            if left in group_files and right in group_files:
                for word in words:
                    if word not in shared_words:
                        shared_words.append(word)
        words = ", ".join(shared_words[:3]) if shared_words else "общие триггерные слова"
        conflicts.append({
            "id": "trigger_overlap",
            "level": "warning",
            "title": f"Пересечение триггерных слов ({_names_text(group)})",
            "desc": (
                f"У этих LoRA есть общие триггеры ({words}). Один и тот же запрос "
                "включает сразу несколько эффектов, и результат трудно предсказать. "
                "Оставьте одну модель или разнесите их по разным сценам."
            ),
            "culprits": culprits,
            "action": {
                "type": "keep_one",
                "target": group[0]["file"],
                "culprits": culprits,
                "label": f"Оставить только {group[0]['name']}",
            },
        })

    # 6. Weight far above the recommended value for this family.
    over = []
    for item in _unique_by_file(parsed):
        recommended = item["recommended_weight"]
        strength = item["strength"]
        if recommended > 0 and strength >= recommended * 2 and strength >= recommended + 0.35:
            over.append((item, recommended))
    if over:
        target = min(recommended for _, recommended in over)
        details = "; ".join(
            f"«{item['name']}»: {item['strength']:.2f} при рекомендации {recommended:.2f}"
            for item, recommended in over[:3]
        )
        conflicts.append({
            "id": "weight_over_recommended",
            "level": "warning",
            "title": f"Сила LoRA заметно выше рекомендуемой ({len(over)})",
            "desc": (
                f"{details}. Слишком высокий вес усиливает артефакты и подавляет "
                "остальные LoRA. Начните с рекомендованного значения и повышайте постепенно."
            ),
            "culprits": [item["file"] for item, _ in over],
            "action": {
                "type": "scale_down",
                "target": f"{target:.2f}",
                "label": f"Снизить силу до {target:.2f}",
            },
        })

    return conflicts


def format_loras_for_agent(active_loras, custom_meta=None):
    """Format active LoRAs into an instruction block for the prompt-writing agent."""
    if not active_loras:
        return ""

    lines = []
    seen = set()
    for item in active_loras:
        if isinstance(item, dict):
            if not item.get("on", True):
                continue
            file_name = str(item.get("file") or "")
            strength = _as_float(item.get("strength"), 0.8)
        else:
            file_name = str(item or "")
            strength = 0.8
        if not file_name:
            continue
        key = Path(file_name).name.lower()
        if key in seen:
            continue
        seen.add(key)

        info = get_lora_metadata(file_name, custom_meta=custom_meta)
        triggers = info.get("triggers") or []
        if triggers:
            trigger_text = f"Триггерные слова: {', '.join(triggers)}"
        else:
            trigger_text = "Триггерные слова не требуются: модель управляет стилем, светом или движением"

        lines.append(
            f"- LoRA «{info['name']}» (файл: {info['filename']}, сила: {strength:.2f}):\n"
            f"  Назначение: {info['desc']}\n"
            f"  {trigger_text}"
        )

    if not lines:
        return ""

    conflicts = analyze_lora_conflicts(active_loras, custom_meta=custom_meta)
    conflict_notes = ""
    if conflicts:
        conflict_lines = [f"- {c['title']}: {c['desc']}" for c in conflicts]
        conflict_notes = (
            "\nВНИМАНИЕ: НАЙДЕНЫ КОНФЛИКТЫ МЕЖДУ ВКЛЮЧЁННЫМИ LORA:\n"
            + "\n".join(conflict_lines)
            + "\nЕсли сцена пользователя пересекается с предупреждением, коротко "
              "предупредите его в конце ответа.\n\n"
        )

    rule = (
        "КАК ИСПОЛЬЗОВАТЬ АКТИВНЫЕ LORA В ПРОМПТЕ:\n"
        "1. Перечисленные LoRA уже включены в генерацию. Если тема сцены, персонаж, "
        "стиль, свет, ракурс или движение совпадают с назначением LoRA — обязательно "
        "используй её точные триггерные слова в английском описании "
        "integrated_multimodal_description.\n"
        "2. Вплетай триггеры органично, как часть описания сцены, а не отдельным списком.\n"
        "3. Не добавляй триггеры, если сцена не имеет к ним отношения. Если сцена "
        "соответствует назначению LoRA, использование триггеров обязательно."
    )

    return (
        "ПОДКЛЮЧЁННЫЕ И АКТИВНЫЕ LORA В ПРОЕКТЕ:\n"
        + "\n".join(lines)
        + "\n\n"
        + conflict_notes
        + rule
        + "\n"
    )
