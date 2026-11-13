"""Projects: the workspaces the window switches between.

A project is a label on every result (see `domain.projects`), not a separate folder or database:
one Library, one queue, one results folder, and every record carries the id of the project it was
made in. The handlers here only list, create and select; the stamping happens at submission
(`App.submission_meta`), so no generation mode can forget it.

Part of the window's server: the methods below are mixed into `application.App` (see server.py).
Each one is an aiohttp handler; the routes themselves are listed in one place, `server.build_app`.
"""

from aiohttp import web


class ProjectRoutes:
    async def h_projects(self, _req):
        return web.json_response({"projects": self.projects(), "active": self.active_project()})

    async def h_project_create(self, req):
        body = await req.json()
        try:
            project = self.create_project((body or {}).get("name"))
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e))
        return web.json_response({"project": project, "projects": self.projects(),
                                  "active": self.active_project()}, status=201)

    async def h_project_active(self, req):
        body = await req.json()
        try:
            active = self.set_active_project((body or {}).get("id"))
        except KeyError:
            raise web.HTTPNotFound(text="Такого проекта нет")
        return web.json_response({"active": active, "projects": self.projects()})
