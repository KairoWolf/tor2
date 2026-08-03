"""SQLite storage for a tor2 server: members, channels, messages, media, invites.

Secrets (member tokens, invite codes) are only ever stored as SHA-256 hashes,
so a stolen database does not let anyone authenticate as an existing member.
"""

import hashlib
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path

HISTORY_PER_CHANNEL = 500
# Blobs kept on disk. Big videos are allowed up to 3 GB each, so this has to
# be roomy; the real backstop is the 80%-disk check in the daemon.
MEDIA_TOTAL_CAP = int(os.environ.get("TOR2_MEDIA_CAP_BYTES",
                                     20 * 1024 * 1024 * 1024))
CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,23}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS members(
    id INTEGER PRIMARY KEY,
    token_hash TEXT UNIQUE NOT NULL,
    nick TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS channels(
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS media(
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    path TEXT NOT NULL,
    created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    nick TEXT NOT NULL,
    ts REAL NOT NULL,
    body TEXT,
    media_id INTEGER REFERENCES media(id) ON DELETE SET NULL);
CREATE INDEX IF NOT EXISTS idx_msg_chan ON messages(channel_id, id);
CREATE TABLE IF NOT EXISTS invites(
    code_hash TEXT PRIMARY KEY,
    uses_left INTEGER NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL);
"""


def hash_secret(value: str) -> str:
    return hashlib.sha256(f"tor2-secret:{value}".encode()).hexdigest()


def new_code() -> str:
    """A short, readable invite code (no ambiguous characters)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join(
        "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))


class ServerDB:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.media_dir = data_dir / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "server.db")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self.db.commit()
        if not self.channels():
            self.create_channel("general")

    # ---------- meta ----------

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.db.commit()

    # ---------- invites ----------

    def create_invite(self, uses: int = 1, is_admin: bool = False) -> str:
        code = new_code()
        self.db.execute(
            "INSERT INTO invites(code_hash,uses_left,is_admin,created) VALUES(?,?,?,?)",
            (hash_secret(code), uses, int(is_admin), time.time()))
        self.db.commit()
        return code

    def redeem_invite(self, code: str) -> bool | None:
        """Consume one use. Returns is_admin, or None if the code is invalid."""
        row = self.db.execute(
            "SELECT code_hash, uses_left, is_admin FROM invites WHERE code_hash=?",
            (hash_secret(code.strip().lower()),)).fetchone()
        if row is None or row["uses_left"] <= 0:
            return None
        if row["uses_left"] == 1:
            self.db.execute("DELETE FROM invites WHERE code_hash=?", (row["code_hash"],))
        else:
            self.db.execute(
                "UPDATE invites SET uses_left=uses_left-1 WHERE code_hash=?",
                (row["code_hash"],))
        self.db.commit()
        return bool(row["is_admin"])

    # ---------- members ----------

    def create_member(self, nick: str, is_admin: bool) -> tuple[int, str]:
        token = secrets.token_urlsafe(24)
        nick = self.unique_nick(nick)
        cur = self.db.execute(
            "INSERT INTO members(token_hash,nick,is_admin,created) VALUES(?,?,?,?)",
            (hash_secret(token), nick, int(is_admin), time.time()))
        self.db.commit()
        return cur.lastrowid, token

    def member_by_token(self, token: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM members WHERE token_hash=?", (hash_secret(token),)).fetchone()

    def member_by_nick(self, nick: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM members WHERE nick=? COLLATE NOCASE", (nick,)).fetchone()

    def unique_nick(self, nick: str) -> str:
        base, n = nick, 2
        while self.member_by_nick(nick):
            nick = f"{base}{n}"
            n += 1
        return nick

    def remove_member(self, member_id: int) -> None:
        self.db.execute("DELETE FROM members WHERE id=?", (member_id,))
        self.db.commit()

    def member_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) c FROM members").fetchone()["c"]

    # ---------- channels ----------

    def channels(self) -> list[str]:
        return [r["name"] for r in
                self.db.execute("SELECT name FROM channels ORDER BY id")]

    def channel_id(self, name: str) -> int | None:
        row = self.db.execute("SELECT id FROM channels WHERE name=?", (name,)).fetchone()
        return row["id"] if row else None

    def create_channel(self, name: str) -> None:
        if not CHANNEL_RE.match(name):
            raise ValueError("channel names: lowercase letters, digits, - and _ (max 24)")
        if self.channel_id(name) is not None:
            raise ValueError(f"#{name} already exists")
        self.db.execute("INSERT INTO channels(name,created) VALUES(?,?)",
                        (name, time.time()))
        self.db.commit()

    def delete_channel(self, name: str) -> None:
        if len(self.channels()) <= 1:
            raise ValueError("cannot delete the last channel")
        if self.channel_id(name) is None:
            raise ValueError(f"no channel #{name}")
        self.db.execute("DELETE FROM channels WHERE name=?", (name,))
        self.db.commit()

    # ---------- messages ----------

    def add_message(self, channel: str, member_id: int | None, nick: str,
                    body: str | None, media_id: int | None = None) -> dict:
        cid = self.channel_id(channel)
        if cid is None:
            raise ValueError(f"no channel #{channel}")
        ts = time.time()
        cur = self.db.execute(
            "INSERT INTO messages(channel_id,member_id,nick,ts,body,media_id) "
            "VALUES(?,?,?,?,?,?)", (cid, member_id, nick, ts, body, media_id))
        self.db.commit()
        self._prune(cid)
        return {"id": cur.lastrowid, "chan": channel, "nick": nick,
                "ts": ts, "body": body, "media": self.media_info(media_id)}

    def _prune(self, cid: int) -> None:
        self.db.execute(
            "DELETE FROM messages WHERE channel_id=? AND id NOT IN "
            "(SELECT id FROM messages WHERE channel_id=? ORDER BY id DESC LIMIT ?)",
            (cid, cid, HISTORY_PER_CHANNEL))
        self.db.commit()

    def history(self, channel: str, limit: int = 50) -> list[dict]:
        cid = self.channel_id(channel)
        if cid is None:
            return []
        rows = self.db.execute(
            "SELECT * FROM messages WHERE channel_id=? ORDER BY id DESC LIMIT ?",
            (cid, min(limit, HISTORY_PER_CHANNEL))).fetchall()
        return [{"id": r["id"], "chan": channel, "nick": r["nick"], "ts": r["ts"],
                 "body": r["body"], "media": self.media_info(r["media_id"])}
                for r in reversed(rows)]

    # ---------- media ----------

    def add_media(self, kind: str, ext: str, blob: bytes, sha: str) -> int:
        cur = self.db.execute(
            "INSERT INTO media(kind,ext,size,sha256,path,created) VALUES(?,?,?,?,'',?)",
            (kind, ext, len(blob), sha, time.time()))
        mid = cur.lastrowid
        path = self.media_dir / f"{mid:06d}.{ext}"
        path.write_bytes(blob)
        self.db.execute("UPDATE media SET path=? WHERE id=?", (str(path), mid))
        self.db.commit()
        self._evict_media()
        return mid

    def add_media_file(self, kind: str, ext: str, sink) -> int:
        """Adopt a completed :class:`~tor2.media.ChunkSink` without reading it.

        Raises ValueError (and discards the temp file) if it fails validation.
        """
        cur = self.db.execute(
            "INSERT INTO media(kind,ext,size,sha256,path,created) VALUES(?,?,?,?,'',?)",
            (kind, ext, sink.size, sink.sha, time.time()))
        mid = cur.lastrowid
        path = self.media_dir / f"{mid:06d}.{ext}"
        try:
            sink.finish(path)
        except (ValueError, OSError):
            self.db.execute("DELETE FROM media WHERE id=?", (mid,))
            self.db.commit()
            raise
        self.db.execute("UPDATE media SET path=? WHERE id=?", (str(path), mid))
        self.db.commit()
        self._evict_media()
        return mid

    def media_path(self, media_id: int) -> Path | None:
        r = self.db.execute("SELECT path FROM media WHERE id=?", (media_id,)).fetchone()
        return Path(r["path"]) if r and r["path"] else None

    def media_info(self, media_id: int | None) -> dict | None:
        if media_id is None:
            return None
        r = self.db.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
        if r is None:
            return None
        return {"id": r["id"], "kind": r["kind"], "ext": r["ext"],
                "size": r["size"], "sha256": r["sha256"]}

    def media_bytes(self, media_id: int) -> bytes | None:
        r = self.db.execute("SELECT path FROM media WHERE id=?", (media_id,)).fetchone()
        if r is None:
            return None
        p = Path(r["path"])
        return p.read_bytes() if p.is_file() else None

    def _evict_media(self) -> None:
        """Drop oldest blobs until the store is under the disk cap."""
        total = self.db.execute("SELECT COALESCE(SUM(size),0) s FROM media").fetchone()["s"]
        if total <= MEDIA_TOTAL_CAP:
            return
        for r in self.db.execute("SELECT id,path,size FROM media ORDER BY id"):
            Path(r["path"]).unlink(missing_ok=True)
            self.db.execute("DELETE FROM media WHERE id=?", (r["id"],))
            total -= r["size"]
            if total <= MEDIA_TOTAL_CAP:
                break
        self.db.commit()

    def close(self) -> None:
        self.db.close()
