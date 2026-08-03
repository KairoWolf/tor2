"""tor2 server daemon — self-hosted Discord-style channels over Tor.

Run with:  python -m tor2.server [data-dir]

Every member holds an encrypted tunnel to this daemon (the same double-encrypted
session the DM mode uses). Unlike DMs, the server necessarily decrypts messages
in order to route and store them — you are trusting whoever runs the machine,
which is why it is meant to be self-hosted.
"""

import argparse
import asyncio
import base64
import binascii
import logging
import re
import os
import shutil
import signal
import time
import sqlite3
import sys
from pathlib import Path

from . import atrest, media, proto, updater, video
from .serverdb import ServerDB
from .tornet import TorNet

log = logging.getLogger("tor2.server")

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "tor2-server"
HISTORY_ON_JOIN = 50
JOIN_CODE_PERSON = "tor2-server-join-v1"
UPDATE_CHECK_INTERVAL = 24 * 60 * 60
VERSION = "4"   # protocol/daemon generation, for staleness checks
RATE_WINDOW = 10.0        # seconds
RATE_MAX = 15             # messages per window per member
AUTH_FAIL_WINDOW = 300.0  # seconds
AUTH_FAIL_MAX = 10        # bad invites/tokens before new attempts are refused


def clean_nick(raw: str) -> str:
    return re.sub(r"[^\w\-]", "", str(raw))[:32] or "member"


class Client:
    """One connected member."""

    def __init__(self, session: proto.Session, server: "Tor2Server"):
        self.session = session
        self.server = server
        self.member_id: int | None = None
        self.nick = "?"
        self.is_admin = False
        self.channel = "general"
        self.upload: dict | None = None
        self.recent: list[float] = []      # timestamps, for flood control
        self.out: asyncio.Queue = asyncio.Queue(maxsize=128)

    @property
    def authed(self) -> bool:
        return self.member_id is not None

    def enqueue(self, msg: dict) -> None:
        """Never block the sender: a member on a slow circuit gets dropped
        frames rather than stalling the whole server."""
        try:
            self.out.put_nowait(msg)
        except asyncio.QueueFull:
            log.warning("output queue full for %s, dropping message", self.nick)

    async def writer_loop(self) -> None:
        while True:
            msg = await self.out.get()
            try:
                await self.session.send(msg)
            finally:
                self.out.task_done()

    async def flush(self) -> None:
        """Best-effort: push anything still queued before the socket closes,
        so a client learns *why* it was rejected."""
        while not self.out.empty():
            try:
                await asyncio.wait_for(self.session.send(self.out.get_nowait()),
                                       timeout=5)
            except Exception:
                return


class Tor2Server:
    def __init__(self, data_dir: Path, passphrase: str | None = None):
        self.data_dir = data_dir
        key = atrest.load_key(data_dir, passphrase)
        self.vault = atrest.Vault(key)
        self.key_fp = atrest.key_fingerprint(key)
        self.db = ServerDB(data_dir, vault=self.vault)
        self.tor = TorNet(data_dir / "tordata")
        self.clients: set[Client] = set()
        self.uploads: dict[int, dict] = {}   # member id -> in-flight upload
        self.auth_failures: list[float] = []  # timestamps, for brute-force defence
        self.published_codes: dict[str, str] = {}
        self.local_port = 0
        self.name = self.db.get_meta("name") or "tor2-server"
        self.closing = False
        self.restart_requested = False
        self.main_task: asyncio.Task | None = None

    # ---------- lifecycle ----------

    async def start(self) -> None:
        log.info("starting tor…")
        await asyncio.to_thread(
            self.tor.launch, lambda p: log.info("tor bootstrap %d%%", p))
        server = await asyncio.start_server(self.handle_conn, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        log.info("publishing onion service…")
        onion = await asyncio.to_thread(
            self.tor.publish_persistent_onion, port, self.data_dir / "onion.key")
        self.db.set_meta("onion", onion)

        first_run = self.db.member_count() == 0
        admin_invite = None
        if first_run and not self.db.get_meta("admin_invite_issued"):
            admin_invite = self.db.create_invite(uses=1, is_admin=True)
            self.db.set_meta("admin_invite_issued", "1")

        print("\n" + "=" * 68, flush=True)
        print(f"  tor2 server “{self.name}” is running", flush=True)
        print(f"  address: {onion}", flush=True)
        if admin_invite:
            print(f"  ADMIN INVITE (one use): {admin_invite}", flush=True)
            print("  join with:  /joinserver "
                  f"{onion} {admin_invite}", flush=True)
        print(f"  channels: {', '.join('#' + c for c in self.db.channels())}", flush=True)
        print(f"  storage:  encrypted at rest (key {self.key_fp})", flush=True)
        print("  for lawful use only — see README", flush=True)
        print("=" * 68 + "\n", flush=True)

        self.local_port = port
        self.db.set_meta("daemon_version", VERSION)
        self.db.set_meta("heartbeat", str(time.time()))
        asyncio.create_task(self.heartbeat_loop())
        await self.publish_join_codes()
        asyncio.create_task(self.update_loop())
        asyncio.create_task(self.watch_join_codes())
        async with server:
            await server.serve_forever()

    async def heartbeat_loop(self) -> None:
        """A timestamp other commands can read to tell if we are running."""
        while True:
            await asyncio.sleep(30)
            try:
                self.db.set_meta("heartbeat", str(time.time()))
            except Exception:
                return

    async def publish_join_codes(self) -> None:
        """Give every live join code its own temporary address.

        The digits derive the address, so someone holding the code can reach
        the server without being told anything else.
        """
        for entry in self.db.active_join_codes():
            await self.publish_one_code(entry["code"])

    async def publish_one_code(self, code: str) -> str | None:
        if code in self.published_codes:
            return self.published_codes[code]
        try:
            addr = await asyncio.to_thread(
                self.tor.publish_code_onion, code, self.local_port,
                JOIN_CODE_PERSON)
        except Exception as e:
            log.warning("could not publish join code %s: %s", code, e)
            return None
        self.published_codes[code] = addr
        log.info("join code %s is live", code)
        return addr

    async def unpublish_code(self, code: str) -> None:
        if self.published_codes.pop(code, None):
            await asyncio.to_thread(self.tor.remove_code_onion, code)

    def shutdown(self) -> None:
        self.closing = True
        self.clients.clear()
        self.db.close()
        self.tor.shutdown()

    # ---------- connections ----------

    async def handle_conn(self, reader, writer) -> None:
        try:
            session = await proto.handshake(reader, writer)
        except Exception as e:
            log.info("handshake failed: %s", e)
            writer.close()
            return
        client = Client(session, self)
        wtask = asyncio.create_task(client.writer_loop())
        try:
            await session.send({"t": "srvhello", "name": self.name,
                                "proto": 3})
            while True:
                msg = await session.recv()
                await self.dispatch(client, msg)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as e:
            log.info("client %s error: %s", client.nick, e)
        finally:
            self.clients.discard(client)
            try:  # let queued output (e.g. the rejection reason) go out first
                await asyncio.wait_for(client.out.join(), timeout=5)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            wtask.cancel()
            await client.flush()
            await session.close()
            if client.authed:
                log.info("%s disconnected", client.nick)
                self.broadcast_members()

    async def dispatch(self, c: Client, msg: dict) -> None:
        kind = msg["t"]
        if kind == "ping":
            c.enqueue({"t": "pong"})
            return
        if kind == "pong":
            return
        if not c.authed:
            if kind != "auth":
                raise proto.ProtocolError("auth required")
            await self.do_auth(c, msg)
            return
        handler = {
            "post": self.do_post,
            "switch": self.do_switch,
            "history": self.do_history,
            "mput": self.do_mput,
            "mchunk": self.do_mchunk,
            "fetch": self.do_fetch,
            "del": self.do_delete,
            "mkchan": self.do_mkchan,
            "rmchan": self.do_rmchan,
            "newinvite": self.do_newinvite,
            "joincode": self.do_joincode,
            "kick": self.do_kick,
            "ban": self.do_ban,
            "unban": self.do_unban,
            "bans": self.do_bans,
            "promote": self.do_promote,
            "demote": self.do_demote,
            "autoupdate": self.do_autoupdate,
        }.get(kind)
        if handler is None:
            return  # DM-only message type; ignore on a server
        await handler(c, msg)

    # ---------- auth ----------

    def db_codes_left(self) -> set[str]:
        return {e["code"] for e in self.db.active_join_codes()}

    def auth_locked(self) -> bool:
        """Refuse guessing sprees at invite codes and tokens."""
        now = asyncio.get_event_loop().time()
        self.auth_failures = [t for t in self.auth_failures
                              if now - t < AUTH_FAIL_WINDOW]
        return len(self.auth_failures) >= AUTH_FAIL_MAX

    def note_auth_failure(self) -> None:
        self.auth_failures.append(asyncio.get_event_loop().time())

    async def do_auth(self, c: Client, msg: dict) -> None:
        if self.auth_locked():
            c.enqueue({"t": "srverr", "msg":
                       "too many failed join attempts recently — try again later"})
            raise proto.ProtocolError("auth locked")
        nick = clean_nick(msg.get("nick", ""))
        token = str(msg.get("token", ""))[:200]
        invite = str(msg.get("invite", ""))[:64]

        if token:
            row = self.db.member_by_token(token)
            if row is None:
                self.note_auth_failure()
                c.enqueue({"t": "srverr", "msg":
                           "your membership is no longer valid — ask for a new invite"})
                raise proto.ProtocolError("bad token")
            if self.db.is_banned(row["nick"]):
                c.enqueue({"t": "srverr", "msg": "you are banned from this server"})
                raise proto.ProtocolError("banned")
            c.member_id, c.nick, c.is_admin = row["id"], row["nick"], bool(row["is_admin"])
            new_token = None
        else:
            if self.db.is_banned(nick):
                c.enqueue({"t": "srverr",
                           "msg": f"the name “{nick}” is banned from this server"})
                raise proto.ProtocolError("banned")
            code = str(msg.get("code", "")).strip()
            is_admin = None
            if code:
                is_admin = self.db.redeem_join_code(code)
                if is_admin is not None and code not in self.db_codes_left():
                    # spent: take its address down so the digits stop working
                    asyncio.create_task(self.unpublish_code(code))
            if is_admin is None and invite:
                is_admin = self.db.redeem_invite(invite)
            if is_admin is None:
                self.note_auth_failure()
                c.enqueue({"t": "srverr", "msg": "invalid or used-up invite code"})
                raise proto.ProtocolError("bad invite")
            c.member_id, new_token = self.db.create_member(nick, is_admin)
            c.nick = self.db.member_by_token(new_token)["nick"]
            c.is_admin = is_admin

        c.channel = self.db.channels()[0]
        self.clients.add(c)
        c.enqueue({"t": "authok", "nick": c.nick, "admin": c.is_admin,
                   "token": new_token, "channels": self.db.channels(),
                   "channel": c.channel, "server": self.name,
                   # A join code's address is temporary and disappears once the
                   # code is spent, so tell the client the permanent one to
                   # remember instead.
                   "address": self.db.get_meta("onion") or ""})
        c.enqueue({"t": "histbatch", "chan": c.channel,
                   "msgs": self.db.history(c.channel, HISTORY_ON_JOIN)})
        log.info("%s authenticated%s", c.nick, " (admin)" if c.is_admin else "")
        self.broadcast_members()

    # ---------- chat ----------

    def rate_limited(self, c: Client) -> bool:
        """Stop one member flooding history out of existence."""
        now = asyncio.get_event_loop().time()
        c.recent = [t for t in c.recent if now - t < RATE_WINDOW]
        if len(c.recent) >= RATE_MAX:
            return True
        c.recent.append(now)
        return False

    async def do_post(self, c: Client, msg: dict) -> None:
        body = str(msg.get("body", ""))[:4000].strip()
        chan = str(msg.get("chan", c.channel))
        if not body or self.db.channel_id(chan) is None:
            return
        if self.rate_limited(c):
            c.enqueue({"t": "srverr", "msg":
                       f"slow down — max {RATE_MAX} messages per "
                       f"{int(RATE_WINDOW)} seconds"})
            return
        stored = self.db.add_message(chan, c.member_id, c.nick, body)
        self.broadcast({"t": "event", **stored})

    async def do_switch(self, c: Client, msg: dict) -> None:
        chan = str(msg.get("chan", ""))
        if self.db.channel_id(chan) is None:
            c.enqueue({"t": "srverr", "msg": f"no channel #{chan}"})
            return
        c.channel = chan
        c.enqueue({"t": "histbatch", "chan": chan,
                   "msgs": self.db.history(chan, HISTORY_ON_JOIN)})
        self.broadcast_members()

    async def do_history(self, c: Client, msg: dict) -> None:
        chan = str(msg.get("chan", c.channel))
        try:
            before = int(msg.get("before") or 0) or None
        except (TypeError, ValueError):
            before = None
        c.enqueue({"t": "histbatch", "chan": chan, "append": bool(before),
                   "msgs": self.db.history(chan, HISTORY_ON_JOIN, before=before)})

    # ---------- media ----------

    async def do_mput(self, c: Client, msg: dict) -> None:
        try:
            kind = str(msg.get("kind", ""))
            if kind not in ("img", "vid", "aud"):
                raise ValueError("bad media kind")
            size = int(msg.get("size", 0))
            chunks = int(msg.get("chunks", 0))
            cap = {"img": proto.MAX_IMAGE_BYTES,
                   "aud": proto.MAX_AUDIO_BYTES}.get(
                       kind, proto.MAX_BIG_VIDEO_BYTES)
            if not (0 < size <= cap):
                raise ValueError(f"too large (max {cap // 1024 // 1024} MB)")
            if not media.plausible_chunk_count(size, chunks):
                raise ValueError("bad chunk count")
            ext = re.sub(r"[^a-z0-9]", "", str(msg.get("ext", ""))[:5]) or {
                "img": "png", "aud": "mp3"}.get(kind, "mp4")
            chan = str(msg.get("chan", c.channel))
            if self.db.channel_id(chan) is None:
                raise ValueError("no such channel")
            self.check_storage(size)
        except ValueError as e:
            c.enqueue({"t": "srverr", "msg": f"upload rejected: {e}"})
            return

        if c.upload is not None:      # a new upload supersedes an abandoned one
            c.upload["sink"].abort()
        sink = media.ChunkSink(size, chunks, str(msg.get("sha256", ""))[:64],
                               ext=ext, tmp_dir=self.db.media_dir)
        thumb = None
        raw = msg.get("thumb")
        if raw:
            try:
                thumb = base64.b64decode(raw, validate=True)
                if len(thumb) > video.MAX_THUMB_BYTES:
                    thumb = None
            except (binascii.Error, ValueError):
                thumb = None
        # A sender-supplied name is a display label only — it never touches
        # any path we open.
        name = re.sub(r"[^\w .\-()\[\]]", "", str(msg.get("name", ""))[:80]).strip()
        c.upload = {"kind": kind, "ext": ext, "chan": chan, "sink": sink,
                    "thumb": thumb, "name": name}
        # shared by member id so extra circuits can feed the same upload
        self.uploads[c.member_id] = c.upload
        if size > proto.MAX_VIDEO_BYTES:
            log.info("%s uploading %s (%.1f MB)", c.nick, ext, size / 1024 / 1024)

    def check_storage(self, incoming: int) -> None:
        """Refuse uploads that would push the disk past the limit."""
        d = self.db.data_dir
        used = media.used_fraction(d)
        if used >= proto.SERVER_DISK_LIMIT:
            raise ValueError(
                f"server storage is {used * 100:.0f}% full — ask the admin to "
                "free space")
        if not media.room_for(d, incoming):
            raise ValueError("not enough free space on the server for this file")
        st = shutil.disk_usage(d)
        if (st.used + incoming) / st.total >= proto.SERVER_DISK_LIMIT:
            raise ValueError(
                f"this upload would push the server past "
                f"{proto.SERVER_DISK_LIMIT * 100:.0f}% disk usage")

    async def do_mchunk(self, c: Client, msg: dict) -> None:
        up = c.upload or self.uploads.get(c.member_id)
        if up is None:
            return
        sink = up["sink"]
        try:
            data = msg.get("bin")
            if data is None:                 # older peers sent base64
                data = base64.b64decode(msg.get("data", ""), validate=True)
            off = msg.get("off")
            sink.write(data, offset=int(off) if off is not None else None)
        except (binascii.Error, ValueError, OSError, TypeError) as e:
            sink.abort()
            c.upload = None
            self.uploads.pop(c.member_id, None)
            c.enqueue({"t": "srverr", "msg": f"upload rejected: {e}"})
            return
        if not sink.complete:
            return

        c.upload = None
        self.uploads.pop(c.member_id, None)
        try:
            mid = self.db.add_media_file(up["kind"], up["ext"], sink)
        except ValueError as e:
            c.enqueue({"t": "srverr", "msg": f"upload rejected: {e}"})
            return
        if up.get("thumb"):
            self.db.set_thumb(mid, up["thumb"])
        if up.get("name"):
            self.db.set_name(mid, up["name"])
        stored = self.db.add_message(up["chan"], c.member_id, c.nick, None, mid)
        # Images are small enough to push to everyone; videos are announced and
        # fetched on demand so one upload doesn't flood every Tor circuit.
        if up["kind"] == "img":
            blob = self.db.media_bytes(mid)
            self.broadcast({"t": "event", **stored,
                            "inline": base64.b64encode(blob).decode()})
        else:
            self.broadcast({"t": "event", **stored})

    async def do_fetch(self, c: Client, msg: dict) -> None:
        try:
            mid = int(msg.get("id", 0))
        except (TypeError, ValueError):
            return
        info = self.db.media_info(mid)
        path = self.db.media_path(mid) if info else None
        if not info or path is None or not path.is_file():
            c.enqueue({"t": "srverr", "msg": f"no media #{mid}"})
            return
        if msg.get("thumb"):
            # /preview: a still costs a few KB instead of the whole file
            if info.get("thumb"):
                c.enqueue({"t": "mthumb", "id": mid, "kind": info["kind"],
                           "name": info.get("name", ""),
                           "size": info["size"], "thumb": info["thumb"]})
            else:
                c.enqueue({"t": "srverr",
                           "msg": f"no preview stored for #{mid} — /get {mid}"})
            return
        # Read from disk as we go: a 3 GB download must not be materialized.
        chunk_size = proto.chunk_size_for(info["size"])
        try:
            start = max(0, int(msg.get("start") or 0))
            end = int(msg.get("end") or info["size"])
        except (TypeError, ValueError):
            start, end = 0, info["size"]
        end = min(end, info["size"])
        if start >= end:
            return
        ranged = (start, end) != (0, info["size"])
        n_chunks = max(1, (info["size"] + chunk_size - 1) // chunk_size)
        if not ranged:       # only the first request describes the whole file
            c.enqueue({"t": "mget", "id": mid, "kind": info["kind"],
                       "ext": info["ext"], "size": info["size"],
                       "sha256": info["sha256"], "chunks": n_chunks})
        asyncio.create_task(
            self.stream_media(c, path, chunk_size, mid, start, end))

    async def stream_media(self, c: Client, path: Path, chunk_size: int,
                           mid: int, start: int = 0, end: int | None = None) -> None:
        """Write chunks straight to the socket rather than through the outbound
        queue: queueing a multi-gigabyte download would buffer it in memory,
        and Session.send already serializes frames, so chat still interleaves.
        """
        try:
            pos = start
            buf = bytearray()
            for plain in self.db.vault.open_file_iter(path, start, end):
                buf += plain
                while len(buf) >= chunk_size:
                    if c not in self.clients:
                        return
                    piece = bytes(buf[:chunk_size])
                    del buf[:chunk_size]
                    await c.session.send_binary(
                        {"t": "mgchunk", "id": mid, "off": pos}, piece)
                    pos += len(piece)
            if buf and c in self.clients:
                await c.session.send_binary(
                    {"t": "mgchunk", "id": mid, "off": pos}, bytes(buf))
        except (OSError, ConnectionError, asyncio.CancelledError) as e:
            log.info("media #%s stream to %s ended: %s", mid, c.nick, e)

    async def do_delete(self, c: Client, msg: dict) -> None:
        try:
            mid = int(msg.get("id", 0))
        except (TypeError, ValueError):
            return
        row = self.db.message(mid)
        if row is None:
            c.enqueue({"t": "srverr", "msg": f"no message {mid}"})
            return
        if row["member_id"] != c.member_id and not c.is_admin:
            c.enqueue({"t": "srverr", "msg": "you can only delete your own messages"})
            return
        self.db.delete_message(mid)
        log.info("%s deleted message %s", c.nick, mid)
        self.broadcast({"t": "deleted", "id": mid, "chan": row["chan"],
                        "by": c.nick})

    # ---------- admin ----------

    def _require_admin(self, c: Client) -> bool:
        if not c.is_admin:
            c.enqueue({"t": "srverr", "msg": "admin only"})
            return False
        return True

    async def do_mkchan(self, c: Client, msg: dict) -> None:
        if not self._require_admin(c):
            return
        try:
            self.db.create_channel(str(msg.get("name", "")).strip().lower())
        except ValueError as e:
            c.enqueue({"t": "srverr", "msg": str(e)})
            return
        self.broadcast_channels()

    async def do_rmchan(self, c: Client, msg: dict) -> None:
        if not self._require_admin(c):
            return
        name = str(msg.get("name", "")).strip().lower()
        try:
            self.db.delete_channel(name)
        except ValueError as e:
            c.enqueue({"t": "srverr", "msg": str(e)})
            return
        fallback = self.db.channels()[0]
        for cl in self.clients:
            if cl.channel == name:
                cl.channel = fallback
                cl.enqueue({"t": "histbatch", "chan": fallback,
                            "msgs": self.db.history(fallback, HISTORY_ON_JOIN)})
        self.broadcast_channels()

    async def do_newinvite(self, c: Client, msg: dict) -> None:
        if not self._require_admin(c):
            return
        try:
            uses = max(1, min(100, int(msg.get("uses", 1))))
        except (TypeError, ValueError):
            uses = 1
        code = self.db.create_invite(uses=uses, is_admin=bool(msg.get("admin")))
        c.enqueue({"t": "srverr", "msg":
                   f"new invite: {code}  ({uses} use{'s' if uses > 1 else ''})"})

    async def do_joincode(self, c: Client, msg: dict) -> None:
        """Mint an 8-digit code that is address and invite in one."""
        if not self._require_admin(c):
            return
        arg = str(msg.get("mode", "")).strip().lower()
        if arg == "list":
            live = self.db.active_join_codes()
            if not live:
                c.enqueue({"t": "srverr", "msg": "no join codes are live"})
                return
            listing = ", ".join(
                f"{e['code']} ({e['uses_left']} use"
                f"{'s' if e['uses_left'] != 1 else ''}"
                f"{', admin' if e['is_admin'] else ''})" for e in live)
            c.enqueue({"t": "srverr", "msg": f"live join codes: {listing}"})
            return
        if arg.startswith("revoke"):
            code = arg.split()[-1]
            ok = self.db.revoke_join_code(code)
            if ok:
                await self.unpublish_code(code)
            c.enqueue({"t": "srverr",
                       "msg": f"revoked {code}" if ok else f"no such code {code}"})
            return

        parts = arg.split()
        uses = next((int(p) for p in parts if p.isdigit()), 1)
        hours = 24
        for p in parts:
            if p.endswith("h") and p[:-1].isdigit():
                hours = int(p[:-1])
        code = self.db.create_join_code(uses=max(1, min(100, uses)),
                                        is_admin="admin" in parts,
                                        ttl_seconds=hours * 3600)
        addr = await self.publish_one_code(code)
        if addr is None:
            self.db.revoke_join_code(code)
            c.enqueue({"t": "srverr", "msg": "could not publish a join code"})
            return
        c.enqueue({"t": "srverr", "msg":
                   f"join code: {code}  —  they type this alone, nothing else. "
                   f"{uses} use(s), expires in {hours}h"})

    async def do_kick(self, c: Client, msg: dict) -> None:
        if not self._require_admin(c):
            return
        nick = clean_nick(msg.get("nick", ""))
        row = self.db.member_by_nick(nick)
        if row is None:
            c.enqueue({"t": "srverr", "msg": f"no member “{nick}”"})
            return
        if row["id"] == c.member_id:
            c.enqueue({"t": "srverr", "msg": "you cannot kick yourself"})
            return
        self.db.remove_member(row["id"])
        for cl in list(self.clients):
            if cl.member_id == row["id"]:
                cl.enqueue({"t": "srverr", "msg": "you were removed from this server"})
                cl.member_id = None
                self.clients.discard(cl)
        c.enqueue({"t": "srverr", "msg": f"kicked “{nick}”"})
        self.broadcast_members()

    async def check_update(self, c: "Client | None" = None,
                           force: bool = False) -> None:
        """Fast-forward to the latest release and exit so the service manager
        restarts us on the new code. Only ever called when an admin turned
        auto-update on (or asked for it explicitly)."""
        if not force and self.db.get_meta("autoupdate") != "1":
            return
        try:
            info = await asyncio.to_thread(updater.check)
        except updater.UpdateError as e:
            if c:
                c.enqueue({"t": "srverr", "msg": f"update check failed: {e}"})
            log.info("update check failed: %s", e)
            return
        if not info["behind"]:
            if c:
                c.enqueue({"t": "srverr", "msg":
                           f"already up to date ({info['current']})"})
            return

        log.info("updating: %d commit(s) behind %s", info["behind"], info["branch"])
        if c:
            c.enqueue({"t": "srverr", "msg":
                       f"updating to {info['latest']} ({info['behind']} new "
                       "commit(s)) — the server will restart"})
        try:
            await asyncio.to_thread(updater.apply)
        except updater.UpdateError as e:
            log.warning("update failed: %s", e)
            if c:
                c.enqueue({"t": "srverr", "msg": f"update failed: {e}"})
            return
        self.broadcast({"t": "srverr",
                        "msg": "server is restarting to apply an update"})
        await asyncio.sleep(1)
        log.info("restarting to apply update")
        self.restart_requested = True
        if self.main_task is not None:   # stops serve_forever; main() then exits
            self.main_task.cancel()

    async def watch_join_codes(self) -> None:
        """Publish codes minted elsewhere — the command line, or another
        admin — without needing a restart."""
        while True:
            await asyncio.sleep(30)
            try:
                live = {e["code"] for e in self.db.active_join_codes()}
                for code in live - set(self.published_codes):
                    await self.publish_one_code(code)
                for code in set(self.published_codes) - live:
                    await self.unpublish_code(code)
            except Exception as e:
                log.debug("join code sweep failed: %s", e)

    async def update_loop(self) -> None:
        """Check daily while auto-update is enabled."""
        while True:
            await asyncio.sleep(UPDATE_CHECK_INTERVAL)
            if self.db.get_meta("autoupdate") == "1":
                try:
                    await self.check_update()
                except Exception as e:
                    log.info("scheduled update check failed: %s", e)

    def disconnect_member(self, member_id: int, why: str) -> None:
        for cl in list(self.clients):
            if cl.member_id == member_id:
                cl.enqueue({"t": "srverr", "msg": why})
                cl.member_id = None
                self.clients.discard(cl)

    async def do_ban(self, c: Client, msg: dict) -> None:
        if not self._require_admin(c):
            return
        nick = clean_nick(msg.get("nick", ""))
        reason = str(msg.get("reason", ""))[:200]
        row = self.db.member_by_nick(nick)
        if row and row["id"] == c.member_id:
            c.enqueue({"t": "srverr", "msg": "you cannot ban yourself"})
            return
        if row and row["is_admin"]:
            c.enqueue({"t": "srverr",
                       "msg": f"“{nick}” is an admin — /demote them first"})
            return
        self.db.ban(nick, reason, c.nick)
        if row:
            self.disconnect_member(row["id"], "you were banned from this server")
        c.enqueue({"t": "srverr", "msg": f"banned “{nick}”"
                   + (f" ({reason})" if reason else "")})
        log.info("%s banned %s", c.nick, nick)
        self.broadcast_members()

    async def do_unban(self, c: Client, msg: dict) -> None:
        if not self._require_admin(c):
            return
        nick = clean_nick(msg.get("nick", ""))
        ok = self.db.unban(nick)
        c.enqueue({"t": "srverr", "msg": f"unbanned “{nick}” — they need a new "
                   "invite to rejoin" if ok else f"“{nick}” is not banned"})

    async def do_bans(self, c: Client, msg: dict) -> None:
        if not self._require_admin(c):
            return
        rows = self.db.bans()
        if not rows:
            c.enqueue({"t": "srverr", "msg": "nobody is banned"})
            return
        listing = ", ".join(
            b["nick"] + (f" ({b['reason']})" if b["reason"] else "") for b in rows)
        c.enqueue({"t": "srverr", "msg": f"banned: {listing}"})

    async def do_promote(self, c: Client, msg: dict) -> None:
        if not self._require_admin(c):
            return
        nick = clean_nick(msg.get("nick", ""))
        row = self.db.member_by_nick(nick)
        if row is None:
            c.enqueue({"t": "srverr", "msg": f"no member “{nick}” — they must "
                       "join before you can promote them"})
            return
        if row["is_admin"]:
            c.enqueue({"t": "srverr", "msg": f"“{row['nick']}” is already an admin"})
            return
        self.db.set_admin(row["id"], True)
        for cl in self.clients:
            if cl.member_id == row["id"]:
                cl.is_admin = True
                cl.enqueue({"t": "srverr",
                            "msg": "you are now an admin of this server"})
        c.enqueue({"t": "srverr", "msg": f"promoted “{row['nick']}” to admin"})
        log.info("%s promoted %s to admin", c.nick, row["nick"])

    async def do_demote(self, c: Client, msg: dict) -> None:
        if not self._require_admin(c):
            return
        nick = clean_nick(msg.get("nick", ""))
        row = self.db.member_by_nick(nick)
        if row is None or not row["is_admin"]:
            c.enqueue({"t": "srverr", "msg": f"“{nick}” is not an admin"})
            return
        if len(self.db.admins()) <= 1:
            c.enqueue({"t": "srverr", "msg": "cannot demote the last admin"})
            return
        self.db.set_admin(row["id"], False)
        for cl in self.clients:
            if cl.member_id == row["id"]:
                cl.is_admin = False
                cl.enqueue({"t": "srverr", "msg": "you are no longer an admin"})
        c.enqueue({"t": "srverr", "msg": f"demoted “{row['nick']}”"})

    async def do_autoupdate(self, c: Client, msg: dict) -> None:
        if not self._require_admin(c):
            return
        arg = str(msg.get("mode", "")).strip().lower()
        if arg in ("on", "off"):
            self.db.set_meta("autoupdate", "1" if arg == "on" else "0")
            if arg == "on":
                c.enqueue({"t": "srverr", "msg":
                           "auto-update ON — the server will pull new versions "
                           "from its git remote and restart. Only leave this on "
                           "if you trust that repository completely."})
                self.autoupdate_task = asyncio.create_task(self.check_update(c))
            else:
                c.enqueue({"t": "srverr", "msg": "auto-update OFF"})
            return
        if arg == "now":
            self.autoupdate_task = asyncio.create_task(self.check_update(c, force=True))
            return
        on = self.db.get_meta("autoupdate") == "1"
        c.enqueue({"t": "srverr", "msg":
                   f"auto-update is {'ON' if on else 'OFF'} "
                   "(/autoupdate on|off|now)"})

    # ---------- broadcast ----------

    def broadcast(self, msg: dict) -> None:
        if self.closing:
            return
        for cl in list(self.clients):
            if cl.authed:
                cl.enqueue(msg)

    def broadcast_channels(self) -> None:
        self.broadcast_members()

    def broadcast_members(self) -> None:
        if self.closing:
            return
        self.broadcast({"t": "members", "online": self.online_nicks(),
                        "channels": self.db.channels()})

    def online_nicks(self) -> list[str]:
        return sorted({cl.nick for cl in self.clients if cl.authed})


def db_meta_snapshot(data_dir: Path, passphrase: str | None) -> dict:
    db = ServerDB(data_dir, vault=atrest.Vault(
        atrest.load_key(data_dir, passphrase)))
    try:
        return {"heartbeat": db.get_meta("heartbeat"),
                "version": db.get_meta("daemon_version"),
                "onion": db.get_meta("onion")}
    finally:
        db.close()


def daemon_status(meta: dict) -> str:
    """Say plainly whether a running server will pick this up.

    A code only works once the daemon publishes the address it derives, so a
    stale or stopped daemon means the digits reach nothing — worth saying
    rather than leaving someone staring at "reconnecting".
    """
    try:
        beat = float(meta.get("heartbeat") or 0)
    except (TypeError, ValueError):
        beat = 0
    age = time.time() - beat
    if beat and age < 180:
        if meta.get("version") == VERSION:
            return "A running server will publish it within a minute."
        return ("The running server is an OLD build that cannot publish join "
                "codes. Restart it: systemctl restart tor2-server")
    return ("No running server detected. Start it (systemctl start tor2-server) "
            "— the code cannot be reached until it publishes the address.")


def make_backup(data_dir: Path, dest: Path) -> Path:
    """Archive the database, onion key and media into one .tar.gz.

    Restoring is just untarring it back over an empty data directory: the
    server keeps its address, channels, members and history.
    """
    import tarfile

    if dest.is_dir():
        dest = dest / "tor2-server-backup.tar.gz"
    dest.parent.mkdir(parents=True, exist_ok=True)

    db_file = data_dir / "server.db"
    tmp_db = data_dir / "server.db.backup"
    if db_file.is_file():
        # sqlite3's own backup API: consistent even while the server is running
        src = sqlite3.connect(db_file)
        snap = sqlite3.connect(tmp_db)
        with snap:
            src.backup(snap)
        snap.close()
        src.close()
    try:
        with tarfile.open(dest, "w:gz") as tar:
            if tmp_db.is_file():
                tar.add(tmp_db, arcname="server.db")
            for name in ("onion.key",):
                p = data_dir / name
                if p.is_file():
                    tar.add(p, arcname=name)
            media_dir = data_dir / "media"
            if media_dir.is_dir():
                tar.add(media_dir, arcname="media")
    finally:
        tmp_db.unlink(missing_ok=True)
    dest.chmod(0o600)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tor2.server",
                                 description="Run a self-hosted tor2 server.")
    ap.add_argument("data_dir", nargs="?", default=str(DEFAULT_DATA_DIR),
                    help=f"data directory (default: {DEFAULT_DATA_DIR})")
    ap.add_argument("--name", help="server display name (first run only)")
    ap.add_argument("--invite", action="store_true",
                    help="print a fresh one-use invite code and exit")
    ap.add_argument("--admin-invite", action="store_true",
                    help="print a fresh one-use ADMIN invite code and exit")
    ap.add_argument("--joincode", nargs="?", const="1", metavar="USES",
                    help="print a fresh 8-digit join code (address included) "
                         "and exit; optionally how many uses")
    ap.add_argument("--admin", action="store_true",
                    help="with --joincode: whoever uses it becomes an admin")
    ap.add_argument("--hours", type=int, default=24,
                    help="with --joincode: how long it stays valid (default 24)")
    ap.add_argument("--address", action="store_true",
                    help="print this server's onion address and exit")
    ap.add_argument("--codes", action="store_true",
                    help="list live join codes and whether they are published")
    ap.add_argument("--info", action="store_true",
                    help="print address, name, channels and member count, then exit")
    ap.add_argument("--backup", metavar="DEST",
                    help="write a restorable backup archive and exit")
    ap.add_argument("--passphrase", action="store_true",
                    help="derive the at-rest key from a passphrase (prompted) "
                         "instead of a key file — stronger, but the server "
                         "cannot restart unattended")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    passphrase = os.environ.get("TOR2_PASSPHRASE") or None
    logging.getLogger("stem").setLevel(logging.WARNING)
    data_dir = Path(args.data_dir).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    data_dir.chmod(0o700)

    if args.joincode is not None:
        db = ServerDB(data_dir, vault=atrest.Vault(
            atrest.load_key(data_dir, passphrase)))
        try:
            uses = max(1, min(100, int(args.joincode)))
        except ValueError:
            uses = 1
        code = db.create_join_code(uses=uses, is_admin=args.admin,
                                    ttl_seconds=args.hours * 3600)
        db.close()
        print(code)
        print(f"They type just these digits — no address needed. "
              f"{uses} use{'s' if uses != 1 else ''}, valid {args.hours}h.",
              file=sys.stderr)
        print(daemon_status(db_meta_snapshot(data_dir, passphrase)), file=sys.stderr)
        return 0

    if args.invite or args.admin_invite:
        db = ServerDB(data_dir, vault=atrest.Vault(
            atrest.load_key(data_dir, passphrase)))
        print(db.create_invite(uses=1, is_admin=args.admin_invite))
        db.close()
        return 0

    if getattr(args, "passphrase", False) and not passphrase:
        import getpass as _gp
        passphrase = _gp.getpass("at-rest passphrase: ")
        if not passphrase:
            print("no passphrase given", file=sys.stderr)
            return 1

    if args.backup:
        try:
            dest = make_backup(data_dir, Path(args.backup).expanduser())
        except Exception as e:
            print(f"backup failed: {e}", file=sys.stderr)
            return 1
        print(dest)
        print("Contains the onion identity key — keep it as safe as the server "
              "itself.", file=sys.stderr)
        return 0

    if args.codes:
        meta = db_meta_snapshot(data_dir, passphrase)
        db = ServerDB(data_dir, vault=atrest.Vault(
            atrest.load_key(data_dir, passphrase)))
        live = db.active_join_codes()
        db.close()
        if not live:
            print("no join codes are live")
        for e in live:
            left = int(max(0, e["expires"] - time.time()) // 3600)
            print(f"{e['code']}  {e['uses_left']} use(s)"
                  f"{'  admin' if e['is_admin'] else ''}  expires in {left}h")
        print(daemon_status(meta), file=sys.stderr)
        return 0

    if args.address or args.info:
        db = ServerDB(data_dir, vault=atrest.Vault(
            atrest.load_key(data_dir, passphrase)))
        onion = db.get_meta("onion")
        if not onion:
            print("no address yet — start the server once so tor can publish it",
                  file=sys.stderr)
            db.close()
            return 1
        if args.address:
            print(onion)
        else:
            print(f"name:     {db.get_meta('name') or 'tor2-server'}")
            print(f"address:  {onion}")
            print(f"channels: {', '.join('#' + c for c in db.channels())}")
            print(f"members:  {db.member_count()}")
        db.close()
        return 0

    server = Tor2Server(data_dir, passphrase=passphrase)
    if args.name:
        server.db.set_meta("name", args.name)
        server.name = args.name

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(server.start())
    server.main_task = task
    # Cancel the *serving* task, not a placeholder future — otherwise SIGTERM
    # is silently ignored and `systemctl restart` blocks until it gives up
    # and SIGKILLs us.
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except NotImplementedError:  # non-Unix
            pass
    try:
        loop.run_until_complete(task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        task.cancel()
        log.info("shutting down…")
        restart = server.restart_requested
        server.shutdown()
        loop.close()
    if restart:
        # Exit non-zero so `Restart=on-failure` brings us back on the new code.
        log.info("exiting for restart onto the updated version")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
