"""Minimax H3 Workspace desktop entry point: runs the local server and shows it in its own window.

Falls back to the default browser if the WebView2 window cannot be created.
"""

import asyncio
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

if getattr(sys, "frozen", False):
    # installed app: settings live in the install folder the user chose (see datadir.py), never in the Windows profile
    import datadir
    os.environ.setdefault("H3STUDIO_DATA", str(datadir.locate(Path(sys.executable).parent, os.environ.get("LOCALAPPDATA", "."))))

from aiohttp import web  # noqa: E402

import instance  # noqa: E402
import server  # noqa: E402


def free_port(start=8765):
    for port in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("no free port")


CLEANUP_TIMEOUT = 45.0   # the whole stop of the server, in seconds (see server.SHUTDOWN_BUDGET)


class ServerThread(threading.Thread):
    """The server in its own thread: its loop, its runner, and one honest shutdown.

    A failure during startup is stored instead of killing the process silently: the window is not
    opened for a server that did not start, and the message says what happened.
    """

    def __init__(self, port):
        super().__init__(daemon=True)
        self.port = port
        self.ready = threading.Event()
        self.loop = None
        self.runner = None
        self.error = None

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.runner = web.AppRunner(server.build_app(port=self.port),
                                        shutdown_timeout=server.HANDLER_DRAIN)
            self.loop.run_until_complete(self.runner.setup())
            self.loop.run_until_complete(web.TCPSite(self.runner, "127.0.0.1", self.port).start())
        except Exception as e:  # noqa: BLE001 - the window must hear about it, not see a dead process
            self.error = f"{type(e).__name__}: {e}"
            self.ready.set()
            self._close_loop()
            return
        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            self._close_loop()

    def _close_loop(self):
        loop, self.loop = self.loop, None
        if loop is None:
            return
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()

    def shutdown(self):
        """Stop the server and wait for its thread.

        cleanup also stops the managed ComfyUI process. The wait is bounded: a slow engine stop must
        not keep the program alive (that process is adopted by a job object and dies with the app),
        and the loop and the thread are closed in every case.
        """
        if self.loop is None or self.runner is None:
            return
        fut = asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop)
        try:
            fut.result(timeout=CLEANUP_TIMEOUT)
        except TimeoutError:
            print(f"Закрытие заняло больше {CLEANUP_TIMEOUT:g} с: движок не остановился вовремя. "
                  "Состояние очереди сохранено, он завершится вместе с программой.", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 - e.g. the engine is slow to stop: the process is ending
            print(f"Закрытие завершилось с ошибкой: {type(e).__name__}: {e}", file=sys.stderr)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.join(CLEANUP_TIMEOUT)


def start_failed(text):
    """Say why the program is not starting, and do not pretend that it started."""
    message = ("Не удалось запустить программу.\n\n" + str(text) +
               "\n\nЕсли это повторяется, пришлите этот текст автору (раздел «Помощь»).")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "Minimax H3 Workspace", 0x10)
    except Exception:
        print(message, file=sys.stderr)


class JsApi:
    # pywebview exposes every public attribute to JS and walks it recursively,
    # so everything except the methods JS may call stays private (underscore)
    def __init__(self, port):
        self._window = None
        self._port = port

    def pick_folder(self, start=""):
        import webview
        res = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=start if os.path.isdir(start) else "")
        return res[0] if res else None

    @staticmethod
    def _last_dir():
        """The folder the user saved into last time (Windows "Save as" starts there next time)."""
        try:
            d = (server.DATA_DIR / "last_save_dir.txt").read_text(encoding="utf-8").strip()
            return d if os.path.isdir(d) else ""
        except OSError:
            return ""

    @staticmethod
    def _remember_dir(folder):
        try:
            if folder and os.path.isdir(folder):
                (server.DATA_DIR / "last_save_dir.txt").write_text(folder, encoding="utf-8")
        except OSError:
            pass

    def _copy(self, item_id, dest):
        """Stream a library video from the local server into the file the user chose."""
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{self._port}/api/file/{item_id}", timeout=60) as r, \
                open(dest, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)

    @staticmethod
    def _free_name(folder, name):
        stem, ext = os.path.splitext(name)
        path, n = os.path.join(folder, name), 1
        while os.path.exists(path):  # never overwrite files the user already has
            path, n = os.path.join(folder, f"{stem} ({n}){ext}"), n + 1
        return path

    def save_video(self, item_id, filename):
        """The usual Windows "Save as" window, then the video is copied there."""
        import webview
        ext = os.path.splitext(filename)[1].lower() or ".mp4"
        kinds = (f"Картинка {ext[1:].upper()} (*{ext})",) if ext != ".mp4" else ("Видео MP4 (*.mp4)",)
        res = self._window.create_file_dialog(webview.SAVE_DIALOG, directory=self._last_dir(), save_filename=filename, file_types=kinds)
        if not res:
            return {"ok": False, "cancelled": True}
        dest = res if isinstance(res, str) else res[0]
        if not dest.lower().endswith(ext):
            dest += ext
        try:
            self._copy(item_id, dest)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        self._remember_dir(os.path.dirname(dest))
        return {"ok": True, "path": dest}

    def save_videos(self, items):
        """Several videos: one folder is chosen, every video is saved into it."""
        import webview
        res = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=self._last_dir())
        if not res:
            return {"ok": False, "cancelled": True}
        folder = res if isinstance(res, str) else res[0]
        self._remember_dir(folder)
        saved, failed = [], 0
        for it in items:
            try:
                path = self._free_name(folder, it["filename"])
                self._copy(it["id"], path)
                saved.append(path)
            except Exception:
                failed += 1
        return {"ok": bool(saved), "folder": folder, "count": len(saved), "failed": failed}


def already_running():
    """The program is open already: say so, and let this copy close itself."""
    other = instance.published(server.DATA_DIR) or {}
    url = other.get("url") or (f"http://127.0.0.1:{other['port']}" if other.get("port") else "")
    text = ("Minimax H3 Workspace уже запущена.\n\n"
            "Одна папка данных не может обслуживаться двумя копиями: вторая запустила бы те же "
            "незавершённые задания заново, а библиотека и настройки перезаписывались бы друг другом."
            + (f"\n\nОткрытая программа: {url}" if url else ""))
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "Minimax H3 Workspace", 0x40)
    except Exception:
        print(text)


def main():
    import ctypes
    if sys.platform == "win32":
        # no Windows error dialogs (e.g. nvidia-smi.exe 0xc0000142 under memory pressure); child processes inherit this
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)  # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX
    if not instance.acquire(server.DATA_DIR):
        already_running()   # one folder, one copy: the system releases this when the process ends
        return 3
    try:
        return _run_window()
    finally:
        instance.release()


def _run_window():
    import ctypes
    port = free_port()
    instance.publish(server.DATA_DIR, port)   # so a second launch can name the running window
    srv = ServerThread(port)
    srv.start()
    started = srv.ready.wait(30)
    if not started or srv.error:
        # no window for a server that is not there: the old code opened a URL that would never answer
        start_failed(srv.error or "Сервер не ответил за 30 секунд.")
        srv.shutdown()
        return 4
    url = f"http://127.0.0.1:{port}"

    def explain_no_webview():
        try:
            ctypes.windll.user32.MessageBoxW(
                0, "Не найден компонент Windows «Microsoft Edge WebView2», он рисует окно программы.\n\n"
                   "Скачайте и установите «WebView2 Runtime» (бесплатно, с сайта Microsoft: microsoft.com/edge/webview2), "
                   "затем запустите программу снова.\n\nПока программа откроется в обычном браузере.",
                "Minimax H3 Workspace", 0x40)
        except Exception:
            pass

    try:
        import webview
        if sys.platform == "win32":
            try:
                import report
                if report.webview2_version().startswith("не найден"):
                    explain_no_webview()
                    raise RuntimeError("no WebView2")
            except ImportError:
                pass
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True  # a plain <a download> also works as a fallback
        except Exception:
            pass
        api = JsApi(port)
        api._window = webview.create_window("Minimax H3 Workspace", url, width=1440, height=920, min_size=(1000, 680), maximized=True,   # opens on the whole screen (the taskbar stays visible)
                                           background_color="#12070a", js_api=api,
                                           draggable=True)  # pywebview blocks dragging of every picture by default; the app drags pictures into slots
        # watchdog for a half-broken WebView2 (e.g. Edge removed by hand): the version check above passes,
        # the window opens but stays black and «loaded» never fires. Offer the browser, like the no-WebView2 fallback.
        loaded = threading.Event()
        state = {"browser": False}
        try:
            api._window.events.loaded += loaded.set
        except Exception:
            loaded.set()
        if sys.platform == "win32":
            def _watchdog():
                if loaded.wait(20):
                    return
                try:
                    r = ctypes.windll.user32.MessageBoxW(
                        0, "Окно программы не загрузилось за 20 секунд. Скорее всего, на компьютере сломан компонент Windows "
                           "«Microsoft Edge WebView2» — он рисует окно программы.\n\n"
                           "Да — открыть интерфейс в обычном браузере (так можно работать прямо сейчас)\n"
                           "Нет — закрыть программу\n\n"
                           "Чтобы окно работало как положено, установите WebView2 Runtime: microsoft.com/edge/webview2",
                        "Minimax H3 Workspace", 0x40 | 0x4)
                    if r == 6:
                        state["browser"] = True
                        webbrowser.open(url)
                except Exception:
                    pass
                try:
                    api._window.destroy()   # makes webview.start() return below
                except Exception:
                    pass
            threading.Thread(target=_watchdog, daemon=True).start()
        webview.start(gui="edgechromium", private_mode=False,
                      storage_path=str(server.DATA_DIR / "webview"))
        if state["browser"]:
            threading.Event().wait()   # keep the server alive for the browser window
    except Exception:
        # no WebView2: use the browser and keep running until the console/process is closed
        webbrowser.open(url)
        threading.Event().wait()
    finally:
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
