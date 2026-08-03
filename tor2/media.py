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


def sha256_file(path: Path, on_progress=None) -> str:
    """Blocking: hash a file without loading it. Run in a thread."""
    h = hashlib.sha256()
    done = 0
    with path.open("rb") as f:
        while chunk := f.read(READ_SIZE):
            h.update(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done)
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
        self._sharded = False
        fd, name = tempfile.mkstemp(prefix="tor2-", suffix=".part",
                                    dir=str(tmp_dir) if tmp_dir else None)
        self.path = Path(name)
        self._fh = os.fdopen(fd, "wb")

    def write(self, data: bytes, offset: int | None = None) -> None:
        """Append, or place at `offset` when shards arrive out of order.

        Raises ValueError if the sender exceeds what it announced.
        """
        if self.got + len(data) > self.size:
            raise ValueError("larger than announced")
        if offset is None:
            self._fh.write(data)
            self._hash.update(data)      # sequential: hash as we go
        else:
            self._fh.seek(offset)
            self._fh.write(data)
            self._sharded = True         # hash at the end instead
        self.got += len(data)
        self.count += 1

    @property
    def complete(self) -> bool:
        return self.got >= self.size if self._sharded else self.count >= self.chunks

    @property
    def progress(self) -> int:
        return self.got * 100 // self.size if self.size else 100

    def verify(self) -> bool:
        if self.got != self.size:
            return False
        if self._sharded:
            # shards land out of order, so hash the finished file instead
            if not self._fh.closed:
                self._fh.flush()
            return sha256_file(self.path) == self.sha
        return self._hash.hexdigest() == self.sha

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
                    on_progress=None, keep_going=None,
                    start: int = 0, end: int | None = None,
                    announce: bool = True, sent_cb=None) -> int:
    """Stream a file as `meta` followed by `chunk_type` frames.

    Bytes travel as raw binary rather than base64, which removes a third of
    the traffic. `start`/`end` send only part of the file, which is how a
    transfer is split across several Tor circuits.
    """
    size = path.stat().st_size
    end = size if end is None else min(end, size)
    n_chunks = max(1, (size + chunk_size - 1) // chunk_size)
    if announce:
        await session.send({**meta, "size": size, "chunks": n_chunks,
                            "sha256": sha})

    sent = 0
    with path.open("rb") as f:
        f.seek(start)
        pos = start
        while pos < end:
            data = f.read(min(chunk_size, end - pos))
            if not data:
                break
            if keep_going is not None and not keep_going():
                raise ConnectionError("transfer aborted")
            await session.send_binary({"t": chunk_type, "off": pos}, data)
            pos += len(data)
            sent += len(data)
            if sent_cb:
                sent_cb(len(data))
            if on_progress:
                on_progress(sent, end - start)
    return sent


def plausible_chunk_count(size: int, chunks: int) -> bool:
    """Guard against a peer announcing an absurd number of tiny chunks."""
    return 0 < chunks <= size // 65536 + 2


class MediaCache:
    """Recently previewed images, so they can be replayed or saved without
    re-downloading. Bounded in both count and bytes."""

    def __init__(self, max_items: int = 12, max_bytes: int = 25 * 1024 * 1024):
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._items: dict[str, tuple[bytes, str]] = {}   # key -> (data, ext)

    def put(self, key: str, data: bytes, ext: str) -> None:
        if len(data) > self.max_bytes:
            return
        self._items.pop(key, None)
        self._items[key] = (data, ext)
        while (len(self._items) > self.max_items
               or sum(len(d) for d, _ in self._items.values()) > self.max_bytes):
            self._items.pop(next(iter(self._items)))

    def get(self, key: str) -> tuple[bytes, str] | None:
        return self._items.get(key)

    @property
    def last_key(self) -> str | None:
        return next(reversed(self._items), None) if self._items else None

    def clear(self) -> None:
        self._items.clear()


class ChunkPlan:
    """A shared queue of byte ranges for several circuits to work through.

    Tor circuits differ wildly in speed. Splitting a file into equal shares
    means the transfer ends when the *slowest* circuit finishes its share
    while the fast ones idle. Handing out small pieces on demand keeps every
    circuit busy until the last byte, so one bad relay costs one piece rather
    than a quarter of the file.
    """

    def __init__(self, size: int, piece: int, done_ranges=None):
        self.size = size
        self.piece = piece
        self.pieces: list[tuple[int, int]] = []
        have = sorted(done_ranges or [])
        pos = 0
        for start, end in have:                # skip what a resume already has
            while pos < min(start, size):
                nxt = min(pos + piece, start, size)
                self.pieces.append((pos, nxt))
                pos = nxt
            pos = max(pos, end)
        while pos < size:
            nxt = min(pos + piece, size)
            self.pieces.append((pos, nxt))
            pos = nxt
        self._next = 0
        self.remaining = sum(b - a for a, b in self.pieces)

    def take(self) -> tuple[int, int] | None:
        if self._next >= len(self.pieces):
            return None
        p = self.pieces[self._next]
        self._next += 1
        return p

    def give_back(self, piece: tuple[int, int]) -> None:
        """A circuit died mid-piece; let another one pick it up."""
        self.pieces.append(piece)

    @property
    def empty(self) -> bool:
        return self._next >= len(self.pieces)


async def send_pieces(session, path: Path, chunk_type: str, plan: ChunkPlan,
                      on_bytes=None, keep_going=None) -> None:
    """Pull ranges off the shared plan until it is empty."""
    with path.open("rb") as f:
        while True:
            piece = plan.take()
            if piece is None:
                return
            start, end = piece
            try:
                f.seek(start)
                pos = start
                while pos < end:
                    if keep_going is not None and not keep_going():
                        raise ConnectionError("transfer aborted")
                    data = f.read(min(READ_SIZE, end - pos))
                    if not data:
                        break
                    await session.send_binary({"t": chunk_type, "off": pos}, data)
                    pos += len(data)
                    if on_bytes:
                        on_bytes(len(data))
            except Exception:
                plan.give_back((pos, end) if pos < end else (start, end))
                raise


class RangeSet:
    """Byte ranges already received, so an interrupted transfer can resume."""

    def __init__(self):
        self.ranges: list[list[int]] = []

    def add(self, start: int, length: int) -> None:
        end = start + length
        merged = []
        placed = False
        for a, b in self.ranges:
            if b < start - 0 or a > end:
                merged.append([a, b])
                continue
            start, end = min(a, start), max(b, end)
        merged.append([start, end])
        merged.sort()
        out: list[list[int]] = []
        for a, b in merged:
            if out and a <= out[-1][1]:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        self.ranges = out

    @property
    def total(self) -> int:
        return sum(b - a for a, b in self.ranges)

    def missing(self, size: int) -> list[tuple[int, int]]:
        gaps, pos = [], 0
        for a, b in self.ranges:
            if a > pos:
                gaps.append((pos, min(a, size)))
            pos = max(pos, b)
        if pos < size:
            gaps.append((pos, size))
        return gaps

    def as_list(self) -> list[tuple[int, int]]:
        return [(a, b) for a, b in self.ranges]
