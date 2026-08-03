"""Streaming media transfer.

Large media never exists in memory as a whole: sends read the file chunk by
chunk, and receives write straight to a temp file that is only moved into
place once its SHA-256 matches. That is what makes multi-gigabyte transfers
possible on a machine with modest RAM.
"""

import base64
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

READ_SIZE = 1024 * 1024
DISK_HEADROOM = 200 * 1024 * 1024   # never fill a disk to the last byte


def sha256_file(path: Path) -> str:
    """Blocking: hash a file without loading it. Run in a thread."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(READ_SIZE):
            h.update(chunk)
    return h.hexdigest()


def free_space(path: Path) -> int:
    probe = path if path.exists() else path.parent
    return shutil.disk_usage(probe).free


def used_fraction(path: Path) -> float:
    st = shutil.disk_usage(path if path.exists() else path.parent)
    return st.used / st.total if st.total else 1.0


def room_for(path: Path, size: int) -> bool:
    return free_space(path) > size + DISK_HEADROOM


class ChunkSink:
    """Collects incoming chunks into a temp file, verifying size and hash."""

    def __init__(self, size: int, chunks: int, sha: str, ext: str = "bin",
                 tmp_dir: Path | None = None):
        self.size = size
        self.chunks = chunks
        self.sha = sha
        self.ext = ext
        self.got = 0
        self.count = 0
        self._hash = hashlib.sha256()
        fd, name = tempfile.mkstemp(prefix="tor2-", suffix=".part",
                                    dir=str(tmp_dir) if tmp_dir else None)
        self.path = Path(name)
        self._fh = os.fdopen(fd, "wb")

    def write(self, data: bytes) -> None:
        """Raises ValueError if the sender exceeds what it announced."""
        if self.got + len(data) > self.size:
            raise ValueError("larger than announced")
        self._fh.write(data)
        self._hash.update(data)
        self.got += len(data)
        self.count += 1

    @property
    def complete(self) -> bool:
        return self.count >= self.chunks

    @property
    def progress(self) -> int:
        return self.got * 100 // self.size if self.size else 100

    def verify(self) -> bool:
        return self.got == self.size and self._hash.hexdigest() == self.sha

    def finish(self, dest: Path) -> None:
        """Close and move into place. Raises ValueError if it did not verify."""
        self._fh.close()
        if not self.verify():
            self.path.unlink(missing_ok=True)
            raise ValueError("checksum mismatch")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(self.path, dest)
        except OSError:  # across filesystems
            shutil.move(str(self.path), str(dest))

    def abort(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
        self.path.unlink(missing_ok=True)


async def send_file(session, path: Path, meta: dict, chunk_type: str,
                    chunk_size: int, sha: str,
                    on_progress=None, keep_going=None) -> int:
    """Stream a file as `meta` followed by `chunk_type` frames.

    `sha` is passed in (hash it in a thread first, so a multi-gigabyte file is
    not hashed on the event loop). Returns the number of bytes sent.
    """
    size = path.stat().st_size
    n_chunks = max(1, (size + chunk_size - 1) // chunk_size)
    await session.send({**meta, "size": size, "chunks": n_chunks, "sha256": sha})

    sent = 0
    with path.open("rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            if keep_going is not None and not keep_going():
                raise ConnectionError("transfer aborted")
            await session.send({"t": chunk_type,
                                "data": base64.b64encode(data).decode()})
            sent += len(data)
            if on_progress:
                on_progress(sent, size)
    return sent


def plausible_chunk_count(size: int, chunks: int) -> bool:
    """Guard against a peer announcing an absurd number of tiny chunks."""
    return 0 < chunks <= size // 65536 + 2
