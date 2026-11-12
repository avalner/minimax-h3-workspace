#!/usr/bin/env python3
"""One command for every check of the project.

    python tests/run.py                      # everything that needs no GPU, no model and no render
    python tests/run.py --only unit          # a single group (see --list)
    python tests/run.py --python "<python.exe>"   # another interpreter for the checks (needs aiohttp)
    python tests/run.py --list               # what is there, without running anything

Every check runs in its own process with its own temporary data folder (H3STUDIO_DATA), its own
agent.json and its own temp directory, so no check can see the user's library, settings or a running
ComfyUI: the API smoke test talks to a fake ComfyUI, the rest never opens a socket.

A check exits 0 when it passed, 1 when it failed, and 3 when it says «cannot run here» (for example
the MCP check without Node or without a running app). A skipped check is printed as such and is not
counted as passed.

Exit code of the runner: 0 all checks passed, 1 something failed, 2 nothing could be run at all.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
# a fresh folder per run, and a random part in the name: a reused process id must not inherit the
# database and the state of an earlier run (a check would then pass or fail for the wrong reason)
TEMP = ROOT / "_test" / "tmp" / f"run-{os.getpid()}-{secrets.token_hex(3)}"

# name -> (title, files, needs_aiohttp, needs_node)
GROUPS = {
    "unit": ("Хранилище, задания, восстановление очереди (без сети)", sorted((TESTS / "unit").glob("test_*.py")), True, False),
    "boot": ("Приложение запускается из исходников и отвечает на свои запросы", [TESTS / "boot_smoke.py"], True, False),
    "graphs": ("Графы генерации не изменились (без ComfyUI)", [TESTS / "video_fused_graph_test.py"], False, False),
    "watchdog": ("Сторож видеокарты, решения по чтениям (без видеокарты)", [TESTS / "gpu_watchdog_test.py"], False, False),
    "api": ("API агента на поддельном ComfyUI (свой временный сервер)", [TESTS / "agent_api_smoke.py"], True, False),
    "ui-state": ("Состояние заданий: state.js под Node (без браузера)", [TESTS / "ui_state_smoke.py"], False, True),
    "ui": ("Окно в браузере: разделы открываются, модули на месте (нужен Chrome/Edge)", [TESTS / "ui_smoke.py"], True, False),
    "mcp": ("MCP-сервер через stdio (нужен node и запущенное приложение)", [TESTS / "mcp_stdio_smoke.py"], False, True),
}
DEFAULT_GROUPS = ["unit", "boot", "graphs", "watchdog", "api", "ui-state", "ui"]
SKIP = 3


def can_import(python, module):
    try:
        code = subprocess.run([python, "-B", "-c", f"import {module}"], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return code.returncode == 0


def run_one(python, path, group, verbose):
    env = {k: v for k, v in os.environ.items() if not k.startswith("H3STUDIO_")}
    search = [str(TESTS), str(TESTS / "unit")]
    if env.get("PYTHONPATH"):
        search.append(env["PYTHONPATH"])
    env.update({
        "H3STUDIO_DATA": str(TEMP / group / path.stem / "data"),
        "H3STUDIO_AGENT_FILE": str(TEMP / group / path.stem / "agent.json"),
        "TMP": str(TEMP / group / path.stem / "tmp"),
        "TEMP": str(TEMP / group / path.stem / "tmp"),
        "PYTHONPATH": os.pathsep.join(search),   # `import support` / `import tempdir` must work
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    for key in ("H3STUDIO_DATA", "TMP", "TEMP"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    Path(env["H3STUDIO_AGENT_FILE"]).parent.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(f"\n--- {path.relative_to(ROOT)} ---", flush=True)
    started = time.monotonic()
    try:
        done = subprocess.run([python, "-B", str(path)], cwd=str(ROOT), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=1800)
    except subprocess.TimeoutExpired:
        return "failed", "не закончил за 30 минут", "", time.monotonic() - started
    took = time.monotonic() - started
    out = (done.stdout or "") + (done.stderr or "")
    if verbose:
        print(out.rstrip(), flush=True)
    if done.returncode == 0:
        return "ok", "", out, took
    if done.returncode == SKIP:
        return "skipped", (out.strip().splitlines() or ["нет объяснения"])[-1], out, took
    return "failed", f"код {done.returncode}", out, took


def main(argv=None):
    try:   # the console of a Russian Windows is cp1251 and would lose the words below
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Run every check of the project in one go.")
    parser.add_argument("--only", action="append", choices=sorted(GROUPS), help="run only this group (repeatable)")
    parser.add_argument("--all", action="store_true", help="include the MCP check (needs Node and a running app)")
    parser.add_argument("--python", default=sys.executable, help="interpreter for the checks (default: this one)")
    parser.add_argument("--list", action="store_true", help="show the groups and exit")
    parser.add_argument("--verbose", "-v", action="store_true", help="print the output of every check")
    args = parser.parse_args(argv)

    if args.list:
        for name, (title, files, needs_aiohttp, needs_node) in GROUPS.items():
            print(f"{name:9} {len(files):2} проверк(и)  {title}")
        return 0

    chosen = args.only or (DEFAULT_GROUPS + (["mcp"] if args.all else []))
    TEMP.mkdir(parents=True, exist_ok=True)

    has_aiohttp = can_import(args.python, "aiohttp")
    print(f"Python: {args.python}")
    print(f"aiohttp: {'есть' if has_aiohttp else 'нет'}    набор: {', '.join(chosen)}")

    results, problems = [], []
    for group in chosen:
        title, files, needs_aiohttp, needs_node = GROUPS[group]
        print(f"\n== {group}: {title}")
        for path in files:
            if not path.exists():
                print(f"  ????  {path.name}: файла нет")
                problems.append(path.name)
                continue
            if needs_aiohttp and not has_aiohttp:
                reason = "нужен aiohttp в этом Python (см. README, раздел о проверках)"
                print(f"  ----  {path.name}: пропущено ({reason})")
                results.append((group, path.name, "skipped", reason, 0.0))
                continue
            if needs_node and not can_import_node(args.python):
                reason = "нужен node в PATH"
                print(f"  ----  {path.name}: пропущено ({reason})")
                results.append((group, path.name, "skipped", reason, 0.0))
                continue
            state, why, out, took = run_one(args.python, path, group, args.verbose)
            mark = {"ok": "ок  ", "failed": "ПЛОХО", "skipped": "----"}[state]
            print(f"  {mark}  {path.name}: {took:.1f} с" + (f"   {why}" if why else ""))
            if state == "failed" and not args.verbose:
                tail = "\n".join(out.strip().splitlines()[-25:])
                print("\n".join("        " + line for line in tail.splitlines()))
            results.append((group, path.name, state, why, took))

    passed = [r for r in results if r[2] == "ok"]
    failed = [r for r in results if r[2] == "failed"]
    skipped = [r for r in results if r[2] == "skipped"]
    print(f"\nИтог: прошло {len(passed)}, не прошло {len(failed)}, пропущено {len(skipped)}")
    for group, name, state, why, _ in failed:
        print(f"  ПЛОХО  {group}/{name}: {why}")
    for group, name, state, why, _ in skipped:
        print(f"  ----  {group}/{name}: {why}")
    if not results:
        return 2
    if failed or problems:
        return 1
    if not passed:
        return 2
    return 0


def can_import_node(python):
    """Whether a `node` can be started at all (the MCP check needs it)."""
    try:
        subprocess.run(["node", "--version"], capture_output=True, timeout=60)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


if __name__ == "__main__":
    sys.exit(main())
