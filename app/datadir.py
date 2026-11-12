"""Where the app keeps its own small files (settings, library list, chats, logs, window cache).

Nothing is left in the Windows profile:
  - before the first setup they live next to the program (<app>\\data), so uninstalling removes them;
  - once the user has chosen an install folder, they move to <install folder>\\data, and the program folder
    keeps only a one-line pointer (data_location.txt), so deleting that folder removes everything.
Older versions kept them in %LOCALAPPDATA%\\MinimaxH3Workspace; they are moved on the first start.
"""
import json
import shutil
from pathlib import Path

from storage.atomic import atomic_write_json, atomic_write_text


def move_all(src, dst):
    dst.mkdir(parents=True, exist_ok=True)
    for item in list(src.iterdir()):
        target = dst / item.name
        if not target.exists():
            shutil.move(str(item), str(target))
    try:
        src.rmdir()
    except OSError:
        pass


def managed_install_dir(data_dir):
    try:
        cfg = json.loads((data_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if cfg.get("engine") == "managed" and cfg.get("install_dir") and Path(cfg["install_dir"]).is_dir():
        return Path(cfg["install_dir"])
    return None


def locate(app_dir, local_appdata):
    app_dir, legacy = Path(app_dir), Path(local_appdata) / "MinimaxH3Workspace"
    pointer, default = app_dir / "data_location.txt", app_dir / "data"
    cur = None
    try:
        if pointer.exists():
            p = Path(pointer.read_text(encoding="utf-8").strip())
            if p.is_dir():
                cur = p
        if cur is None:
            cur = default
            if (legacy / "data").is_dir() and not default.exists():
                move_all(legacy / "data", default)
                if (legacy / "webview").is_dir():
                    move_all(legacy / "webview", default / "webview")
                if (legacy / "llm").is_dir():  # an assistant downloaded by an older version stays where it is
                    cfg_path = default / "config.json"
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
                    if not cfg.get("llm_dir") and cfg.get("engine") != "managed":
                        cfg["llm_dir"] = str(legacy / "llm")
                        atomic_write_json(cfg_path, cfg, indent=1)
                try:
                    legacy.rmdir()
                except OSError:
                    pass
        inst = managed_install_dir(cur)
        if inst and cur != inst / "data":
            move_all(cur, inst / "data")
            cur = inst / "data"
            atomic_write_text(pointer, str(cur))
    except OSError:
        if cur is None or not cur.exists():
            cur = legacy / "data" if (legacy / "data").is_dir() else default
    return cur
