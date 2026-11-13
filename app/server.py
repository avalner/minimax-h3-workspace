"""Minimax H3 Workspace local server: the composition root.

Builds the application object (`application.App`), lists every route the window and the agent
API use, and starts aiohttp on the loopback address. The handlers live in `routes/`, the
shared paths and helpers in `webcommon.py`, the App itself in `application.py`.

    python app/server.py [--port 8765] [--open]
"""

import argparse
import asyncio
import sys
import webbrowser

from aiohttp import web

import instance
from application import App
from version import APP_VERSION
from webcommon import (DATA_DIR, HANDLER_DRAIN, LIBRARY_FILE, QUEUE_FILE, SHUTDOWN_BUDGET,
                       STATE_DB, Library, build_stamp, same_origin_only)

# Re-exported: the launcher, the tests and the contract map import these from this module.
__all__ = ["App", "Library", "build_app", "main", "DATA_DIR", "STATE_DB", "LIBRARY_FILE",
           "QUEUE_FILE", "HANDLER_DRAIN", "SHUTDOWN_BUDGET"]


def build_app(comfy_override=None, port=None):
    state = App(comfy_override, port)
    app = web.Application(client_max_size=300 * 1024 * 1024)
    app.middlewares.append(same_origin_only)  # a foreign page must not drive the server (form POST, WebSocket)
    app.middlewares.append(state.agents.middleware)  # only /api/agent/v1/ is guarded: the window needs no key
    app.on_startup.append(state.start)
    app.on_cleanup.append(state.stop)
    app.add_routes([
        web.get("/", state.h_index),
        web.get("/api/status", state.h_status),
        web.get("/api/system", state.h_system),
        web.get("/api/audio/info", state.h_audio_info),
        web.post("/api/audio/separate", state.h_audio_separate),
        web.get("/api/setup/info", state.h_setup_info),
        web.post("/api/setup/external", state.h_setup_external),
        web.post("/api/setup/install", state.h_setup_install),
        web.post("/api/mode/add", state.h_mode_add),
        web.post("/api/settings/mirror", state.h_mirror),
        web.post("/api/settings/watchdog", state.h_watchdog_set),
        web.post("/api/engine/restart", state.h_engine_restart),
        web.post("/api/mode/offered", state.h_mode_offered),
        web.post("/api/setup/cancel", state.h_setup_cancel),
        web.post("/api/open-driver-page", state.h_open_driver),
        web.post("/api/clipboard", state.h_clipboard),
        web.get("/api/clipboard/has-image", state.h_clipboard_has_image),
        web.route("*", "/api/clipboard/image", state.h_clipboard_image),
        web.post("/api/support", state.h_support),
        web.post("/api/rent/open", state.h_rent_open),
        web.get("/api/rent/info", state.h_rent_info),
        web.get("/api/queue/saved", state.h_queue_saved),
        web.post("/api/queue/resume", state.h_queue_resume),
        web.post("/api/queue/discard", state.h_queue_discard),
        web.get("/api/job/{pid}", state.h_job),
        web.get("/api/input", state.h_input),
        web.post("/api/search-web", state.h_search_web),
        web.post("/api/report", state.h_report),
        web.post("/api/setup/reset", state.h_setup_reset),
        web.post("/api/generate/video", state.h_generate_video),
        web.post("/api/generate/krea", state.h_generate_krea),
        web.post("/api/upscale", state.h_upscale),
        web.post("/api/upscale_file", state.h_upscale_file),
        web.post("/api/grain", state.h_grain),
        web.get("/api/version", lambda _req: web.json_response({"version": APP_VERSION,
                                                                 "built": build_stamp()})),
        web.get("/api/update/check", state.h_update_check),
        web.post("/api/update/install", state.h_update_install),
        web.post("/api/generate/qwen", state.h_generate_qwen),
        web.post("/api/generate/inpaint", state.h_generate_inpaint),
        web.post("/api/generate/lipsync", state.h_generate_lipsync),
        web.get("/api/nodes", state.h_nodes),
        web.get("/api/models/files", state.h_models_files),
        web.post("/api/models/pick", state.h_models_pick),
        web.get("/api/models/compat", state.h_models_compat),
        web.post("/api/loras/custom/mode", state.h_loras_custom_mode),
        web.post("/api/models/open", state.h_models_open),
        web.post("/api/models/download", state.h_models_download),
        web.post("/api/models/download/cancel", state.h_models_download_cancel),
        web.post("/api/models/variant", state.h_models_variant),
        web.get("/api/wf/list", state.h_wf_list),
        web.post("/api/wf/add", state.h_wf_add),
        web.get("/api/wf/{id}", state.h_wf_get),
        web.delete("/api/wf/{id}", state.h_wf_delete),
        web.post("/api/generate/custom", state.h_generate_custom),
        web.get("/api/loras/custom", state.h_loras_custom),
        web.post("/api/loras/custom", state.h_loras_custom_set),
        web.post("/api/generate/refs", state.h_generate_refs),
        web.post("/api/generate/actor", state.h_generate_actor),
        web.post("/api/generate/swap", state.h_generate_swap),
        web.post("/api/generate/long", state.h_generate_long),
        web.post("/api/montage", state.h_montage),
        web.get("/api/ffmpeg/status", state.h_ffmpeg_status),
        web.post("/api/ffmpeg/download", state.h_ffmpeg_download),
        web.post("/api/ffmpeg/download/cancel", state.h_ffmpeg_download_cancel),
        web.post("/api/ffmpeg/set-folder", state.h_ffmpeg_set_folder),
        web.post("/api/ffmpeg/reset-folder", state.h_ffmpeg_reset_folder),
        web.post("/api/ffmpeg/open-folder", state.h_ffmpeg_open_folder),
        web.get("/api/ffmpeg/check-update", state.h_ffmpeg_check_update),
        web.post("/api/ffmpeg/montage", state.h_ffmpeg_montage),
        web.post("/api/ffmpeg/cancel", state.h_ffmpeg_cancel),
        web.get("/api/loras/registry", state.h_loras_registry),
        web.get("/api/loras/conflicts", state.h_loras_conflicts),
        web.post("/api/loras/meta", state.h_loras_meta_save),
        web.get("/api/loras/tokens", state.h_loras_tokens_get),
        web.post("/api/loras/tokens", state.h_loras_tokens_save),
        web.post("/api/loras/open-folder", state.h_loras_open_folder),
        web.post("/api/loras/download", state.h_loras_download),
        web.post("/api/loras/download/cancel", state.h_loras_download_cancel),
        web.get("/api/civitai/search", state.h_civitai_search),
        web.post("/api/civitai/resolve", state.h_civitai_resolve),
        web.get("/api/hf/search", state.h_hf_search),
        web.post("/api/hf/resolve", state.h_hf_resolve),
        web.get("/api/long/state", state.h_long_state),
        web.get("/api/long/project", state.h_long_project),
        web.post("/api/long/project", state.h_long_project),
        web.get("/api/long/ref/{run}/{name}", state.h_long_ref),
        web.post("/api/long/stage", state.h_long_stage),
        web.post("/api/generate/action", state.h_generate_action),
        web.get("/api/action/info", state.h_action_info),
        web.get("/api/cloud", state.h_cloud_get),
        web.post("/api/cloud", state.h_cloud_set),
        web.post("/api/cloud/test", state.h_cloud_test),
        web.post("/api/setup/cloud", state.h_setup_cloud),
        web.post("/api/interrupt", state.h_interrupt),
        web.get("/api/agent/status", state.h_agent_status),
        web.post("/api/agent/translate", state.h_agent_translate),
        web.post("/api/agent/install", state.h_agent_install),
        web.post("/api/agent/install/cancel", state.h_agent_install_cancel),
        web.post("/api/agent/model", state.h_agent_model),
        web.post("/api/agent/variant", state.h_agent_variant),
        web.post("/api/speech/transcribe", state.h_transcribe),
        web.post("/api/speech/folder", state.h_speech_folder),
        web.post("/api/agent/chat", state.h_agent_chat),
        web.get("/api/agent/chats", state.h_chats),
        web.delete("/api/agent/chats", state.h_chats_clear),
        web.get("/api/agent/chats/{id}", state.h_chat_get),
        web.put("/api/agent/chats/{id}", state.h_chat_put),
        web.delete("/api/agent/chats/{id}", state.h_chat_delete),
        web.post("/api/agent/skills", state.h_skill_upload),
        web.patch("/api/agent/skills/{id}", state.h_skill_patch),
        web.delete("/api/agent/skills/{id}", state.h_skill_delete),
        web.get("/api/results", state.h_results_get),
        web.post("/api/results", state.h_results_set),
        web.post("/api/results/backfill", state.h_results_backfill),
        web.post("/api/results/clean", state.h_results_clean),
        web.get("/api/library", state.h_library),
        web.post("/api/preview/keep", state.h_preview_keep),
        web.delete("/api/preview/{id}", state.h_preview_delete),
        web.get("/api/projects", state.h_projects),
        web.post("/api/projects", state.h_project_create),
        web.post("/api/projects/active", state.h_project_active),
        web.get("/api/projects/{id}/assets", state.h_project_assets),
        web.post("/api/projects/{id}/rename", state.h_project_rename),
        web.post("/api/projects/{id}/delete", state.h_project_delete),
        web.get("/api/stages", state.h_stages),
        web.post("/api/stages", state.h_stage_save),
        web.get("/api/stages/{id}/file", state.h_stage_file_get),
        web.post("/api/stages/{id}/file", state.h_stage_file_put),
        web.delete("/api/stages/{id}", state.h_stage_delete),
        web.patch("/api/library/{id}", state.h_library_patch),
        web.delete("/api/library/{id}", state.h_library_delete),
        web.post("/api/library/delete", state.h_library_delete_many),
        web.get("/api/file/{id}", state.h_file),
        web.get("/api/srcfile/{id}", state.h_srcfile),
        web.get("/api/events", state.h_events),
        web.get("/api/agent/allow", state.h_agent_allow),
        web.post("/api/agent/allow", state.h_agent_allow),
        web.get("/ui/{path:.*}", state.h_ui),   # the window's js/css/media, with explicit content types
    ])
    app.add_routes(state.agents.routes())  # /api/agent/v1/...: the contract other programs use
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfy", help="always use this ComfyUI URL (skips the setup wizard)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open the UI in the default browser")
    args = ap.parse_args()

    if not instance.acquire(DATA_DIR):
        other = instance.published(DATA_DIR) or {}
        where = other.get("url") or (f"http://127.0.0.1:{other['port']}" if other.get("port") else "")
        print("Minimax H3 Workspace уже запущена с этой папкой данных: " + str(DATA_DIR)
              + (f"\nОткрытая программа: {where}" if where else ""))
        return 3   # one folder, one copy: a second one would resume the same queue
    try:
        instance.publish(DATA_DIR, args.port)
        app = build_app(args.comfy, args.port)
        if args.open:
            async def open_browser(_app):
                asyncio.get_running_loop().call_later(1, webbrowser.open, f"http://127.0.0.1:{args.port}")
            app.on_startup.append(open_browser)
        web.run_app(app, host="127.0.0.1", port=args.port, print=lambda *_: print(f"Minimax H3 Workspace: http://127.0.0.1:{args.port}"))
    finally:
        instance.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
