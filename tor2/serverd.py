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
import shutil
import signal
import sys
from pathlib import Path

from . import media, proto
from .serverdb import ServerDB
from .tornet import TorNet

log = logging.getLogger("tor2.server")

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "tor2-server"
HISTORY_ON_JOIN = 50


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
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.db = ServerDB(data_dir)
        self.tor = TorNet(data_dir / "tordata")
        self.clients: set[Client] = set()
        self.name = self.db.get_meta("name") or "tor2-server"
        self.closing = False

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
        print("  for lawful use only — see README", flush=True)
        print("=" * 68 + "\n", flush=True)

        async with server:
            await server.serve_forever()

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
            "mkchan": self.do_mkchan,
            "rmchan": self.do_rmchan,
            "newinvite": self.do_newinvite,
            "kick": self.do_kick,
        }.get(kind)
        if handler is None:
            return  # DM-only message type; ignore on a server
        await handler(c, msg)

    # ---------- auth ----------

    async def do_auth(self, c: Client, msg: dict) -> None:
        nick = clean_nick(msg.get("nick", ""))
        token = str(msg.get("token", ""))[:200]
        invite = str(msg.get("invite", ""))[:64]

        if token:
            row = self.db.member_by_token(token)
            if row is None:
                c.enqueue({"t": "srverr", "msg":
                           "your membership is no longer valid — ask for a new invite"})
                raise proto.ProtocolError("bad token")
            c.member_id, c.nick, c.is_admin = row["id"], row["nick"], bool(row["is_admin"])
            new_token = None
        else:
            is_admin = self.db.redeem_invite(invite)
            if is_admin is None:
                c.enqueue({"t": "srverr", "msg": "invalid or used-up invite code"})
                raise proto.ProtocolError("bad invite")
            c.member_id, new_token = self.db.create_member(nick, is_admin)
            c.nick = self.db.member_by_token(new_token)["nick"]
            c.is_admin = is_admin

        c.channel = self.db.channels()[0]
        self.clients.add(c)
        c.enqueue({"t": "authok", "nick": c.nick, "admin": c.is_admin,
                   "token": new_token, "channels": self.db.channels(),
                   "channel": c.channel, "server": self.name})
        c.enqueue({"t": "histbatch", "chan": c.channel,
                   "msgs": self.db.history(c.channel, HISTORY_ON_JOIN)})
        log.info("%s authenticated%s", c.nick, " (admin)" if c.is_admin else "")
        self.broadcast_members()

    # ---------- chat ----------

    async def do_post(self, c: Client, msg: dict) -> None:
        body = str(msg.get("body", ""))[:4000].strip()
        chan = str(msg.get("chan", c.channel))
        if not body or self.db.channel_id(chan) is None:
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
        c.enqueue({"t": "histbatch", "chan": chan,
                   "msgs": self.db.history(chan, HISTORY_ON_JOIN)})

    # ---------- media ----------

    async def do_mput(self, c: Client, msg: dict) -> None:
        try:
            kind = str(msg.get("kind", ""))
            if kind not in ("img", "vid"):
                raise ValueError("bad media kind")
            size = int(msg.get("size", 0))
            chunks = int(msg.get("chunks", 0))
            cap = (proto.MAX_IMAGE_BYTES if kind == "img"
                   else proto.MAX_BIG_VIDEO_BYTES)
            if not (0 < size <= cap):
                raise ValueError(f"too large (max {cap // 1024 // 1024} MB)")
            if not media.plausible_chunk_count(size, chunks):
                raise ValueError("bad chunk count")
            ext = re.sub(r"[^a-z0-9]", "", str(msg.get("ext", ""))[:5]) or (
                "png" if kind == "img" else "mp4")
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
        c.upload = {"kind": kind, "ext": ext, "chan": chan, "sink": sink}
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
        up = c.upload
        if up is None:
            return
        sink = up["sink"]
        try:
            data = base64.b64decode(msg.get("data", ""), validate=True)
            sink.write(data)
        except (binascii.Error, ValueError, OSError) as e:
            sink.abort()
            c.upload = None
            c.enqueue({"t": "srverr", "msg": f"upload rejected: {e}"})
            return
        if not sink.complete:
            return

        c.upload = None
        try:
            mid = self.db.add_media_file(up["kind"], up["ext"], sink)
        except ValueError as e:
            c.enqueue({"t": "srverr", "msg": f"upload rejected: {e}"})
            return
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
        # Read from disk as we go: a 3 GB download must not be materialized.
        chunk_size = proto.chunk_size_for(info["size"])
        n_chunks = max(1, (info["size"] + chunk_size - 1) // chunk_size)
        c.enqueue({"t": "mget", "id": mid, "kind": info["kind"], "ext": info["ext"],
                   "size": info["size"], "sha256": info["sha256"],
                   "chunks": n_chunks})
        asyncio.create_task(self.stream_media(c, path, chunk_size, mid))

    async def stream_media(self, c: Client, path: Path, chunk_size: int,
                           mid: int) -> None:
        """Write chunks straight to the socket rather than through the outbound
        queue: queueing a multi-gigabyte download would buffer it in memory,
        and Session.send already serializes frames, so chat still interleaves.
        """
        try:
            with path.open("rb") as f:
                while chunk := f.read(chunk_size):
                    if c not in self.clients:
                        return
                    await c.session.send({"t": "mgchunk", "id": mid,
                                          "data": base64.b64encode(chunk).decode()})
        except (OSError, ConnectionError, asyncio.CancelledError) as e:
            log.info("media #%s stream to %s ended: %s", mid, c.nick, e)

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
    ap.add_argument("--address", action="store_true",
                    help="print this server's onion address and exit")
    ap.add_argument("--info", action="store_true",
                    help="print address, name, channels and member count, then exit")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("stem").setLevel(logging.WARNING)
    data_dir = Path(args.data_dir).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    data_dir.chmod(0o700)

    if args.invite or args.admin_invite:
        db = ServerDB(data_dir)
        print(db.create_invite(uses=1, is_admin=args.admin_invite))
        db.close()
        return 0

    if args.address or args.info:
        db = ServerDB(data_dir)
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

    server = Tor2Server(data_dir)
    if args.name:
        server.db.set_meta("name", args.name)
        server.name = args.name

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(server.start())
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
        server.shutdown()
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
