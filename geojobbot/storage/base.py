"""Object store interface. R2 is the production implementation; LocalStore is for local runs/tests."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path


class StorageError(Exception):
    """Storage is unreachable or returned an unexpected error (NOT 'object missing')."""


class ConcurrentModificationError(Exception):
    """The object changed since it was read: a conditional write (if_match) was refused."""


class ObjectStore(ABC):
    name = "abstract"

    @abstractmethod
    def get_bytes(self, key: str) -> bytes | None:
        """Return object bytes, or None if the object does not exist. Raise StorageError otherwise."""

    @abstractmethod
    def get_tagged(self, key: str) -> tuple[bytes | None, str | None]:
        """get_bytes plus the ETag the bytes were read at, for a later put_bytes(..., if_match=etag)."""

    @abstractmethod
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream", *,
                  if_match: str | None = None) -> str | None:
        """Write an object atomically and return its ETag (if known). With if_match the write happens only while the
        stored object still has that ETag; otherwise ConcurrentModificationError is raised and nothing is written."""

    @abstractmethod
    def head(self, key: str) -> dict | None:
        """Return {'etag': str, 'size': int} or None if missing."""

    @abstractmethod
    def copy(self, src: str, dst: str) -> None: ...

    @abstractmethod
    def list_keys(self, prefix: str) -> list[str]: ...

    @abstractmethod
    def delete_keys(self, keys: list[str]) -> int: ...


class LocalStore(ObjectStore):
    """Filesystem-backed store with atomic writes (temp file + os.replace)."""

    name = "local"

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise StorageError(f"invalid key {key!r}")
        return path

    @staticmethod
    def _etag(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()  # noqa: S324 - integrity tag, not security

    def get_bytes(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            raise StorageError(str(exc)) from exc

    def get_tagged(self, key: str) -> tuple[bytes | None, str | None]:
        data = self.get_bytes(key)
        return data, (self._etag(data) if data is not None else None)

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream", *,
                  if_match: str | None = None) -> str | None:
        path = self._path(key)
        if if_match is not None:  # emulates R2's If-Match (check, then replace: enough for tests and one local process)
            current = self.head(key)
            if current is None or current["etag"] != if_match:
                raise ConcurrentModificationError(f"{key} changed since it was read")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except OSError as exc:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise StorageError(str(exc)) from exc
        return self._etag(data)

    def head(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.exists():
            return None
        data = path.read_bytes()
        return {"etag": self._etag(data), "size": len(data)}

    def copy(self, src: str, dst: str) -> None:
        s, d = self._path(src), self._path(dst)
        if not s.exists():
            raise StorageError(f"missing {src}")
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(s, d)

    def list_keys(self, prefix: str) -> list[str]:
        out = []
        for path in self.root.rglob("*"):
            if path.is_file() and not path.name.startswith(".tmp-"):
                key = path.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    out.append(key)
        return sorted(out)

    def delete_keys(self, keys: list[str]) -> int:
        count = 0
        for key in keys:
            path = self._path(key)
            if path.exists():
                path.unlink()
                count += 1
        return count
