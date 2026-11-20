"""Reading and writing DBC files: encoding detection, atomic save, change detection."""

from __future__ import annotations

import codecs
import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

# Tried in this order. latin-1 accepts every byte, so decoding never fails and the
# round-trip stays lossless even for files written in GBK, Shift-JIS and the like.
CODECS = ("utf-8", "cp1252", "latin-1")
DEFAULT_CODEC = "cp1252"  # for new files; see PLAN.md 2.3


@dataclass(frozen=True, slots=True)
class FileFormat:
    codec: str = DEFAULT_CODEC
    bom: bool = False


@dataclass(frozen=True, slots=True)
class FileState:
    """What the file on disk looked like when it was last read or written."""

    size: int
    mtime_ns: int
    sha256: str

    @classmethod
    def of(cls, path: Path) -> FileState:
        data = path.read_bytes()
        st = path.stat()
        return cls(st.st_size, st.st_mtime_ns, hashlib.sha256(data).hexdigest())


class ExternalChangeError(Exception):
    """The file was changed (or deleted) by another program since it was read."""

    def __init__(self, path: Path, current: FileState | None) -> None:
        super().__init__(f"{path} wurde seit dem Laden von einem anderen Programm geändert")
        self.path = path
        self.current = current


class SaveError(Exception):
    """Saving failed. If ``temp_path`` is set, the new content is preserved there."""

    def __init__(self, message: str, temp_path: Path | None = None) -> None:
        super().__init__(message)
        self.temp_path = temp_path


def decode(data: bytes) -> tuple[str, FileFormat]:
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8) :].decode("utf-8"), FileFormat("utf-8", bom=True)
    for codec in CODECS:
        try:
            return data.decode(codec), FileFormat(codec)
        except UnicodeDecodeError:
            continue
    raise AssertionError("latin-1 decodes every byte")


def encode(text: str, fmt: FileFormat) -> bytes:
    data = text.encode(fmt.codec)
    return codecs.BOM_UTF8 + data if fmt.bom else data


def can_encode(text: str, fmt: FileFormat) -> bool:
    try:
        text.encode(fmt.codec)
    except UnicodeEncodeError:
        return False
    return True


def read(path: Path) -> tuple[str, FileFormat, FileState]:
    data = path.read_bytes()
    text, fmt = decode(data)
    st = path.stat()
    return text, fmt, FileState(st.st_size, st.st_mtime_ns, hashlib.sha256(data).hexdigest())


def save(path: Path, data: bytes, expected: FileState | None = None, *, backup: bool = False) -> FileState:
    """Write ``data`` to ``path`` atomically.

    expected  State recorded when the file was read. If the content on disk differs
              (or the file is gone), ExternalChangeError is raised and nothing is written.
              None means: the caller knows the file is new, or wants to overwrite.
    backup    Copy the current file to "<name>.bak" before replacing it.

    Symlinks are followed, so the link stays a link. The file mode is kept.
    """
    target = Path(os.path.realpath(path))
    exists = target.exists()
    if expected is not None:
        current = FileState.of(target) if exists else None
        if current is None or current.sha256 != expected.sha256:
            raise ExternalChangeError(path, current)
    if backup and exists:
        shutil.copy2(target, target.with_name(target.name + ".bak"))
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if exists:
            shutil.copymode(target, tmp)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise SaveError(f"{path} konnte nicht geschrieben werden: {exc}") from exc
    try:
        os.replace(tmp, target)
    except PermissionError as exc:
        # Typical on Windows while another program holds the file open.
        raise SaveError(
            f"{path} ist gesperrt (von einem anderen Programm geöffnet?). "
            f"Der neue Inhalt liegt in {tmp}.",
            temp_path=tmp,
        ) from exc
    return FileState.of(target)
