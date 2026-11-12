#!/usr/bin/env python3
"""Smoke test of the MCP server (mcp/h3studio.js): protocol, tools, and — optionally — a real render.

The server is a plain stdio MCP server, so it can be driven directly, without DSH:
it talks to the running Minimax H3 Workspace app through its agent API.

    python tests/mcp_stdio_smoke.py                 # protocol and tools that do not render
    python tests/mcp_stdio_smoke.py --render        # also starts a 3 s 480p job and waits for it

Requires: node in PATH and the app running (its agent.json). Without the app the test
says so and exits with code 3 (skipped), because everything here goes through the app.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp" / "h3studio.js"
TOOLS = {"studio_status", "studio_capabilities", "studio_asset", "studio_image_to_video",
         "studio_job", "studio_wait", "studio_cancel", "studio_library"}

from tempdir import make_temp_dir  # noqa: E402  (the test runs as a script from tests/)


def make_png(path, w=4, h=3):
    raw = b"".join(b"\x00" + bytes([240, 180, 40]) * w for _ in range(h))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class Mcp:
    def __init__(self, node, script, env):
        self.proc = subprocess.Popen([node, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                     env=env, bufsize=1)
        self.lines = queue.Queue()
        self.nid = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.lines.put(json.loads(line))
            except ValueError:
                pass

    def request(self, method, params=None, timeout=60):
        self.nid += 1
        mid = self.nid
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method,
                                          "params": params or {}}, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        end = time.time() + timeout
        while time.time() < end:
            try:
                msg = self.lines.get(timeout=max(0.05, end - time.time()))
            except queue.Empty:
                break
            if msg.get("id") == mid:
                return msg
        raise TimeoutError(f"{method} did not answer in {timeout} s")

    def notify(self, method, params=None):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method,
                                          "params": params or {}}, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def tool(self, name, args=None, timeout=180):
        msg = self.request("tools/call", {"name": name, "arguments": args or {}}, timeout=timeout)
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message"))
        return msg["result"]

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", action="store_true", help="also make a real 3 s clip (slower, uses the GPU)")
    ap.add_argument("--node", default="node")
    args = ap.parse_args()

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    mcp = Mcp(args.node, SERVER, env)
    try:
        init = mcp.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "h3studio-smoke", "version": "1"}})
        info = (init.get("result") or {}).get("serverInfo") or {}
        check("initialize names the server", info.get("name") == "h3studio", str(info))
        check("initialize carries instructions", len((init.get("result") or {}).get("instructions", "")) > 80)
        mcp.notify("notifications/initialized")

        listed = (mcp.request("tools/list").get("result") or {}).get("tools") or []
        names = {t["name"] for t in listed}
        check("all eight tools are listed", names == TOOLS, str(sorted(names)))

        status = mcp.tool("studio_status")
        check("studio_status talks to the running app",
              status.get("isError") is False and "running" in status["content"][0]["text"],
              status["content"][0]["text"][:200])

        caps = mcp.tool("studio_capabilities")
        text = caps["content"][0]["text"]
        check("studio_capabilities shows modes and limits", "modes:" in text and "limits:" in text, text[:200])

        tmp = make_temp_dir("h3s-mcp-")
        png = tmp / "frame.png"
        make_png(png)
        asset = mcp.tool("studio_asset", {"path": str(png)})
        asset_json = asset.get("structuredContent") or {}
        check("studio_asset imports a picture", bool(asset_json.get("asset")) and asset_json.get("kind") == "image",
              str(asset_json)[:200])

        bad = mcp.tool("studio_image_to_video", {"prompt": "тест", "quality": "999p",
                                                 "first_frame": asset_json.get("asset", "")})
        check("a bad parameter comes back as a readable error",
              bad.get("isError") is True and "INVALID_PARAM" in bad["content"][0]["text"],
              bad["content"][0]["text"][:200])

        missing = mcp.tool("studio_job", {"job": "no-such-job"})
        check("an unknown job is an error, not a crash",
              missing.get("isError") is True and "JOB_NOT_FOUND" in missing["content"][0]["text"],
              missing["content"][0]["text"][:200])

        lib = mcp.tool("studio_library", {"limit": 3})
        check("studio_library lists finished work", lib.get("isError") is False, lib["content"][0]["text"][:200])

        if args.render:
            started = mcp.tool("studio_image_to_video", {
                "first_frame": asset_json["asset"],
                "prompt": "summary:\nA single static shot of a test frame: the camera drifts slowly to the right "
                          "while soft rain falls, no cuts.\n\ndetailed_description:\nRealistic photographic style, "
                          "fine film grain. The camera drifts slowly right, rain drifting across the frame.",
                "seconds": 3, "quality": "480p", "aspect": "16:9", "seed": 5,
                "idempotency_key": f"mcp-smoke-{int(time.time())}", "client": "mcp-smoke"})
            job = (started.get("structuredContent") or {}).get("job")
            check("a real job starts through the tool", bool(job) and started.get("isError") is False,
                  started["content"][0]["text"][:200])
            deadline = time.time() + 600
            view = {}
            while time.time() < deadline:
                view = json.loads("{}") if not view else view
                waited = mcp.tool("studio_wait", {"job": job, "timeout_seconds": 90}, timeout=150)
                view = waited.get("structuredContent") or {}
                if view.get("state") not in ("queued", "running"):
                    break
            res = (view.get("results") or [])
            check("the clip is rendered and its file exists",
                  view.get("state") == "done" and res and Path(res[0]["path"]).exists(),
                  f"{view.get('state')} {res[:1]}")
            if res:
                print(f"      clip: {res[0]['path']}")
    finally:
        mcp.close()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
