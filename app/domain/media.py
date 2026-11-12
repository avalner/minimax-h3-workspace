"""A file that a job produced, and how far it got from the engine to the user.

A successful generation is not the same thing as a saved result. The file may still live only in the
engine's output folder, the program may not have copied it yet, the user may not have pressed
«Добавить в библиотеку», and the extra copy in the results folder may have failed. Those are
different states of the same file, and the UI has to be able to tell them apart.

`media_id_for` is what makes registering one engine output twice the same record instead of two:
the id is derived from the job and from the engine's own identity of the file (node, subfolder, type,
filename), so a repeated finalization after a restart lands on the same row.

The service that moves a record from one state to another is `services.media`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePath

from domain.errors import InvalidSpec, SchemaVersionError

SCHEMA_VERSION = 2   # 2: `archive_failed` — the Library record exists, the independent copy does not


class MediaKind(str, Enum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    LATENT = "latent"
    FILE = "file"

    @classmethod
    def of_name(cls, filename) -> "MediaKind":
        suffix = PurePath(str(filename or "")).suffix.lower()
        if suffix in (".mp4", ".webm", ".mkv", ".mov"):
            return cls.VIDEO
        if suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
            return cls.IMAGE
        if suffix in (".wav", ".mp3", ".flac", ".m4a", ".ogg"):
            return cls.AUDIO
        if suffix == ".latent":
            return cls.LATENT
        return cls.FILE


class MediaState(str, Enum):
    BACKEND = "backend"    # only in the engine's output folder (ComfyUI can clean it up)
    LOCAL = "local"        # the program's own copy exists
    DRAFT = "draft"        # waits for «Добавить в библиотеку», not in the Library list
    LIBRARY = "library"    # a Library record points at it
    ARCHIVED = "archived"  # additionally copied into the user's own results folder
    ARCHIVE_FAILED = "archive_failed"  # in the Library, but the independent copy was not made
    MISSING = "missing"    # the file is gone from disk


@dataclass
class MediaRecord:
    media_id: str
    kind: MediaKind = MediaKind.FILE
    state: MediaState = MediaState.BACKEND
    path: str | None = None            # our own copy, when there is one
    backend_file: dict = field(default_factory=dict)   # {"filename", "subfolder", "type"} of the engine
    library_id: str | None = None      # the Library record, when it exists
    bytes: int | None = None
    created_at: float = 0.0
    error: str | None = None

    def __post_init__(self):
        if not str(self.media_id or "").strip():
            raise InvalidSpec("У записи файла нет идентификатора")
        if not isinstance(self.kind, MediaKind) or not isinstance(self.state, MediaState):
            raise InvalidSpec("Неверный тип или состояние файла")

    @property
    def in_library(self) -> bool:
        return self.state in (MediaState.LIBRARY, MediaState.ARCHIVED, MediaState.ARCHIVE_FAILED)

    @property
    def has_copy(self) -> bool:
        return bool(self.path) and self.state in (MediaState.LOCAL, MediaState.DRAFT,
                                                  MediaState.LIBRARY, MediaState.ARCHIVED)

    def to_dict(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "media_id": self.media_id, "kind": self.kind.value,
                "state": self.state.value, "path": self.path, "backend_file": dict(self.backend_file),
                "library_id": self.library_id, "bytes": self.bytes, "created_at": self.created_at,
                "error": self.error}

    @classmethod
    def from_dict(cls, data) -> "MediaRecord":
        version = (data or {}).get("schema_version", 0)
        if version > SCHEMA_VERSION:
            raise SchemaVersionError(f"Файл записан более новой версией ({version} > {SCHEMA_VERSION})")
        return cls(media_id=str(data.get("media_id") or ""), kind=MediaKind(data.get("kind", "file")),
                   state=MediaState(data.get("state", "backend")), path=data.get("path"),
                   backend_file=dict(data.get("backend_file") or {}), library_id=data.get("library_id"),
                   bytes=data.get("bytes"), created_at=float(data.get("created_at") or 0.0),
                   error=data.get("error"))


def media_id_for(job_id, node, file_ref) -> str:
    """The one id of "this output of this job": the same on every retry, after a restart too.

    The engine's identity of a file is node + subfolder + type + filename (that is what the database
    makes unique), and a job is ours. Deriving the id instead of rolling a new one is what makes a
    repeated finalization an update of the same Library item instead of a second copy of the clip.
    """
    ref = file_ref or {}
    identity = json.dumps([str(job_id or ""), str(node or ""), str(ref.get("subfolder") or ""),
                           str(ref.get("type") or "output"), str(ref.get("filename") or "")],
                          ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
