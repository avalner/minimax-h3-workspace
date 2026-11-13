"""Stages: the saved points of a section, kept with the project in the database.

A stage is what «Сохранить этап» remembers — the settings, the reference files, the clip in the
player — and «Этапы» brings it back. It belongs to a project, so it is a record of the project, not
of one browser profile: after a restart and on another day the same project shows the same stages,
with the pictures and audio they were made from (`stages/<id>/…` next to the database). The browser
keeps only the current, unsaved state of the sections for a quick restart.

Part of the window's server: the methods below are mixed into `application.App` (see server.py).
"""

from __future__ import annotations

import asyncio
import mimetypes
import re
import uuid
from datetime import datetime

from aiohttp import web

MAX_PER_MODE = 15   # the same ceiling the section's list had: the newest are kept, old ones go
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")
KEY_RE = re.compile(r"[a-z]{2,12}/[a-z]{2,12}\d{0,2}")   # video/first, refs/img0, lipsync/audio


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
        names = {str(key)[:80]: str(value)[:120] for key, value in (body.get("names") or {}).items()
                 if isinstance(key, str) and isinstance(value, str)} if isinstance(body.get("names"), dict) else {}
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
            "files": files, "names": names, "s": snapshot,
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

    async def h_stage_file_put(self, req):
        """One attached file of a stage: streamed to `stages/<id>/<mode>/<name>` (never into memory)."""
        stage = self.stages_repo.get(req.match_info["id"])
        if stage is None:
            raise web.HTTPNotFound()
        key = str(req.query.get("key") or "")
        if not KEY_RE.fullmatch(key) or key not in (stage.get("files") or []):
            raise web.HTTPBadRequest(text="У этапа нет такого файла")
        reader = await req.multipart()
        part = await reader.next()
        if part is None or part.name != "file":
            raise web.HTTPBadRequest(text="Ожидался файл")
        path = self.stages_repo.file_path(stage["id"], key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".part")
        size = 0
        try:
            sink = await asyncio.to_thread(open, temp, "wb")
            try:
                while True:
                    chunk = await part.read_chunk(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    await asyncio.to_thread(sink.write, chunk)
            finally:
                await asyncio.to_thread(sink.close)
            temp.replace(path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        names = stage.get("names") if isinstance(stage.get("names"), dict) else {}
        name = str(part.filename or names.get(key) or key.split("/")[-1]).replace('"', "")[:120]
        if names.get(key) != name:
            stage["names"] = {**names, key: name}
            self.stages_repo.save(stage)
        return web.json_response({"ok": True, "key": key, "bytes": size, "name": name})

    async def h_stage_file_get(self, req):
        """The file back, under the name it was attached with: the window turns it into a File again."""
        stage = self.stages_repo.get(req.match_info["id"])
        if stage is None:
            raise web.HTTPNotFound()
        key = str(req.query.get("key") or "")
        if not KEY_RE.fullmatch(key) or key not in (stage.get("files") or []):
            raise web.HTTPNotFound()
        path = self.stages_repo.file_path(stage["id"], key)
        if not path.is_file():
            raise web.HTTPNotFound()
        name = str((stage.get("names") or {}).get(key) or key.split("/")[-1]).replace('"', "")[:120]
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return web.FileResponse(path, headers={
            "Content-Type": ctype, "Content-Disposition": f'inline; filename="{name}"'})
