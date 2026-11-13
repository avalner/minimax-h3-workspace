"""The direction of imports: the lower layers must not know about HTTP, the window or the server.

The plan puts it this way: domain does not import server, aiohttp, pywebview or subprocess; services
do not know HTTP; the route groups and the shared helpers do not import the application or the
server (a route that needs the App gets it as `self`, not as a module). This check keeps the rule
from quietly rotting: it reads the imports of every module in the layer, it does not import them.

    python tests/unit/test_layers.py
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import support  # noqa: F401  (the app folder on sys.path and a temporary data folder)

APP = Path(support.APP)

HTTP_AND_WINDOW = {"aiohttp", "web", "pywebview", "flask"}
SERVER = {"server", "application", "routes", "webcommon"}
FORBIDDEN = {
    "domain": HTTP_AND_WINDOW | SERVER | {"subprocess"},
    "services": HTTP_AND_WINDOW | SERVER,
    "storage": HTTP_AND_WINDOW | SERVER,
    "backends": HTTP_AND_WINDOW | SERVER,
    "runtime": HTTP_AND_WINDOW | SERVER,
}


def imports_of(path: Path) -> set[str]:
    """The top-level modules a file imports, whatever the form (import x / from x import y)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def files(folder: str):
    return sorted((APP / folder).glob("*.py"))


class LayerTest(unittest.TestCase):
    def check(self, folder: str, forbidden: set[str]):
        bad = []
        for path in files(folder):
            hits = imports_of(path) & forbidden
            if hits:
                bad.append(f"{folder}/{path.name}: {sorted(hits)}")
        self.assertEqual(bad, [], "слой импортирует то, чего знать не должен")

    def test_domain_knows_nothing_about_the_outside(self):
        self.check("domain", FORBIDDEN["domain"])

    def test_services_know_nothing_about_http(self):
        self.check("services", FORBIDDEN["services"])

    def test_storage_knows_nothing_about_http(self):
        self.check("storage", FORBIDDEN["storage"])

    def test_backends_know_nothing_about_the_server(self):
        self.check("backends", FORBIDDEN["backends"])

    def test_runtime_knows_nothing_about_the_server(self):
        self.check("runtime", FORBIDDEN["runtime"])

    def test_route_groups_do_not_import_each_other_or_the_app(self):
        own = {path.stem for path in files("routes")}
        bad = []
        for path in files("routes"):
            # a group may use `web`/`aiohttp` and `webcommon`; not the app, the server or another group
            forbidden = {"application", "server"} | (own - {path.stem})
            hits = imports_of(path) & forbidden
            if hits:
                bad.append(f"routes/{path.name}: {sorted(hits)}")
        self.assertEqual(bad, [])

    def test_the_shared_helpers_do_not_import_the_app(self):
        hits = imports_of(APP / "webcommon.py") & {"application", "server", "routes"}
        self.assertEqual(hits, set())

    def test_the_developer_report_does_not_import_the_server(self):
        hits = imports_of(APP / "report.py") & {"application", "server", "routes", "webcommon"}
        self.assertEqual(hits, set())


if __name__ == "__main__":
    unittest.main()
