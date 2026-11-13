"""Stages: the saved points of a section, kept with the project in the database.

A stage is what «Сохранить этап» remembers — the settings, the reference files' names, the clip in
the player — and «Этапы» brings it back. It belongs to a project, so it is a record of the project,
not of one browser profile: after a restart, in another window and on another day the same project
shows the same stages. The files themselves stay in the browser (IndexedDB, keyed by the stage id):
they are the user's own pictures and audio, not results of the program.

Part of the window's server: the methods below are mixed into `application.App` (see server.py).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from aiohttp import web

MAX_PER_MODE = 15   # the same ceiling the section's list had: the newest are kept, old ones go
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")


def _text(value, limit):
    return str(value or "").strip()[:limit]


class StageRoutes:
    async def h_stages(self, _req):
        return web.json_response({"stages": self.stages_repo.load()})

    async def h_stage_save(self, req):
        body = await req.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Ожидался объект этапа")
        mode = _text(body.get("mode"), 40)
        if not mode:
            raise web.HTTPBadRequest(text="У этапа нет раздела")
        # an id comes from the browser only when the window imports its old stages: their attached
        # files are keyed by that id, so it must survive the move
        stage_id = _text(body.get("id"), 40)
        if stage_id and not ID_RE.fullmatch(stage_id):
            stage_id = ""
        stage_id = stage_id or uuid.uuid4().hex[:10]
        project = _text(body.get("project"), 40) or self.active_project()
        if not self.projects_repo.exists(project):
            project = self.active_project()
        snapshot = body.get("s") if isinstance(body.get("s"), dict) else {}
        files = [str(key)[:80] for key in (body.get("files") or []) if isinstance(key, str)][:50]
        try:
            created = datetime.fromisoformat(str(body.get("date") or "").replace("Z", "+00:00")).timestamp()
        except ValueError:
            created = datetime.now().timestamp()
        stage = {
            "id": stage_id, "project": project, "mode": mode,
            "name": _text(body.get("name"), 120) or "Этап",
            "auto": bool(body.get("auto")),
            "title": _text(body.get("title"), 200),
            "prompt": _text(body.get("prompt"), 4000),
            "date": _text(body.get("date"), 40) or datetime.now().isoformat(timespec="seconds"),
            "created": created,
            "files": files, "s": snapshot,
        }
        run = _text(body.get("run"), 60)   # the long studio restores its continuation points by it
        if run:
            stage["run"] = run
        with self.db.transaction():
            if stage["auto"]:
                self.stages_repo.drop_auto(project, mode)
            self.stages_repo.save(stage)
            self.stages_repo.prune(project, mode, MAX_PER_MODE)
        return web.json_response({"stage": stage, "stages": self.stages_repo.load()}, status=201)

    async def h_stage_delete(self, req):
        self.stages_repo.delete(req.match_info["id"])
        return web.json_response({"ok": True, "stages": self.stages_repo.load()})
