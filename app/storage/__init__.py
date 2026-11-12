"""Storage helpers that are not tied to any one kind of data.

`atomic` is about writing a small file so that a crash, a power cut or a locked file never leaves
half a document behind. It knows nothing about jobs, media or HTTP.
"""
