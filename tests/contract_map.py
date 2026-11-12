#!/usr/bin/env python3
"""A map of what the refactoring has to keep: HTTP routes, websocket events, and the places that
change job/library state. Printed as markdown, so it can be committed next to the plan.

    python tests/contract_map.py > docs/refactoring/contracts.md

The route list comes from the real application object, so it is exact. The lists of events and of
state-changing places are found by scanning the sources: they are a starting point for the review of
each stage, not a generated truth.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"

from tempdir import make_temp_dir  # noqa: E402

os.environ.setdefault("H3STUDIO_DATA", str(make_temp_dir("h3s-contracts-")))
os.environ.setdefault("H3STUDIO_AGENT_FILE", str(Path(os.environ["H3STUDIO_DATA"]) / "agent.json"))
sys.path.insert(0, str(APP))

import server  # noqa: E402

SOURCES = [APP / "server.py", APP / "agentapi.py", APP / "engine.py", APP / "ffmpeg_mgr.py",
           APP / "agent.py", APP / "updater.py", APP / "catalog.py", APP / "loras_registry.py"]
# the layers the refactoring moved the work into: a map that only sees server.py stops being a map
for package in ("services", "storage", "backends", "domain", "runtime"):
    SOURCES += sorted(p for p in (APP / package).glob("*.py") if p.is_file())


def routes():
    app = server.build_app(port=8765)
    out = set()
    for resource in app.router.resources():
        info = resource.get_info()
        if info.get("prefix"):   # a static mount
            out.add(("STATIC", info["prefix"] + "*", "статические файлы"))
            continue
        path = info.get("path") or getattr(resource, "canonical", "?")
        for route in resource:   # a resolved route: .method and .handler
            if route.method == "HEAD":   # aiohttp adds HEAD for every GET; not part of the contract
                continue
            handler = getattr(route, "handler", None)
            out.add((route.method, path, getattr(handler, "__name__", "?")))
    return sorted(out, key=lambda r: (r[0], r[1]))


def scan(pattern):
    found = []
    for path in SOURCES:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(pattern, line):
                name = str(path.relative_to(APP)).replace("\\", "/")
                found.append((name, n, line.strip()))
    return found


def table(rows, header):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(cell).replace("|", "\\|") for cell in row) + " |")
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("# Карта контрактов и точек изменения состояния")
    print()
    print("Сгенерировано: `python tests/contract_map.py > docs/refactoring/contracts.md`.")
    print("Список маршрутов взят из собранного приложения; события и места изменения состояния")
    print("найдены поиском по исходникам (ориентир для ревью, не гарантия полноты).")
    print()

    rows = routes()
    print(f"## HTTP: {len(rows)} маршрутов")
    print()
    groups = {}
    for method, path, handler in rows:
        key = path.strip("/").split("/")[0] if path.strip("/") else "(корень)"
        groups.setdefault(key, []).append((method, path, handler))
    for key in sorted(groups):
        print(f"### /{key}" if key != "(корень)" else "### /")
        print()
        print("\n".join(table(groups[key], ["метод", "путь", "обработчик"])))
        print()

    events = sorted({m.group(1) for m in (re.search(r'"type":\s*"([a-z_0-9]+)"', line)
                                          for _, _, line in scan(r'"type":\s*"')) if m})
    print(f"## События в окно (broadcast/SSE): {len(events)}")
    print()
    print("\n".join(f"- `{name}`" for name in events))
    print()

    for title, pattern, header in [
        ("Состояние заданий", r"self\.jobs\[|self\.jobs\.pop|job\[.state.\]|job\.get\(.state.|"
                              r"record\.(apply|succeed|fail|cancel_confirmed)\(",
         ["файл", "строка", "код"]),
        ("Черновики и Библиотека", r"self\.pending\b|self\.library\.(repo\.)?(add|remove|save|get)|"
                                   r"self\.library\.items\.insert",
         ["файл", "строка", "код"]),
        ("Короткая память об исходах", r"self\.recent\[|self\.history\[|self\.remember\(",
         ["файл", "строка", "код"]),
        ("Фоновые задачи", r"create_task\(|tasks\.spawn\(|asyncio\.to_thread\(", ["файл", "строка", "код"]),
        ("Запись на диск", r"atomic_write_json\(|atomic_write_text\(|atomic_write_bytes\(|\.write_text\(|\.write_bytes\(",
         ["файл", "строка", "код"]),
    ]:
        found = scan(pattern)
        print(f"## {title}: {len(found)}")
        print()
        print("\n".join(table(found[:200], header)))
        if len(found) > 200:
            print()
            print(f"(показаны первые 200 из {len(found)})")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
