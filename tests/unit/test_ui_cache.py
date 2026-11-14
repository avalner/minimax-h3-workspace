"""Reloads receive fresh UI code even with stale browser cache validators."""
import os
import unittest
from unittest.mock import patch

import support
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from routes.status import StatusRoutes


class UICacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_reload_ignores_old_validators_and_reads_current_code(self):
        root = support.new_dir("ui-cache")
        routes = StatusRoutes()
        app = web.Application()
        app.router.add_get("/", routes.h_index)
        app.router.add_get("/ui/{path:.*}", routes.h_ui)
        async with TestClient(TestServer(app)) as client:
            with patch("routes.status.UI_DIR", root):
                for name in ("index.html", "layout.css", "layout.js", "layout.json"):
                    with self.subTest(name=name):
                        path = root / name
                        path.write_bytes(b"old")
                        stamp = path.stat()
                        url = "/" if name == "index.html" else "/ui/" + name
                        first = await client.get(url)
                        self.assertEqual(await first.read(), b"old")
                        path.write_bytes(b"new")
                        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                        response = await client.get(url, headers={
                            "If-Modified-Since": "Wed, 01 Jan 2031 00:00:00 GMT",
                            "If-None-Match": "*",
                        })
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
                        self.assertEqual(await response.read(), b"new")


if __name__ == "__main__":
    unittest.main()
