"""What runs beside the request handlers: background coroutines and the engine process.

`tasks` is the supervisor of every background coroutine: it keeps the reference, sees the error and
can stop it. `engines` is the owner of ComfyUI: it starts, replaces and stops the process, and it is
the only place that decides who may do that.

Nothing here imports aiohttp, the window or `server`, so the shutdown rules can be tested without an
application.
"""
