"""What the application is about, without a webserver, a window or a GPU.

`jobs` is the state of a generation job and the rules of its transitions; `media` is a file that a
job produced or was made from; `errors` is what a service may refuse with. Nothing here imports
aiohttp, pywebview, subprocess or the server, so the rules can be tested on their own.
"""
