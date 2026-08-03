"""Client-side server mode: joining a tor2 server and using its channels.

Mixed into :class:`~tor2.app.Tor2App`. Kept separate from the direct-message
logic so the two flows stay easy to read independently.
"""

import asyncio
import base64
import hashlib
import tempfile
import time
from pathlib import Path

from rich.text import Text
from textual.widgets import Static

from . import media, proto, store, video
from .imgview import render_preview, validate_image
from .tornet import normalize_onion

# imported lazily from app to avoid a circular import at module load
from .app_consts import RECEIVED_DIR, fmt_size

SERVER = "server"
HANDSHAKE_TIMEOUT = 60


class ChannelItem(Static):
    """One clickable channel row in the sidebar."""

    def __init__(self, channel: str):
        super().__init__(f"# {channel}", classes="chan")
        self.channel = channel

    def on_click(self) -> None:
        self.app.run_worker(self.app.server_switch(self.channel), exclusive=False)


class ServerModeMixin:
    """Requires the host class to provide: session, state, sys_msg, chat,
    set_status, tor, nick, _require_connected, _next_received."""

    # ---------- joining ----------

    async def join_server(self, arg: str) -> None:
        """/joinserver <onion> <invite> [local-name]"""
        parts = arg.split()
        if len(parts) < 2:
            self.sys_msg("usage: /joinserver <onion> <invite-code> [name]", "red")
            return
        onion, invite = parts[0], parts[1]
        local = parts[2] if len(parts) > 2 else ""
        try:
            onion = normalize_onion(onion)
        except ValueError as e:
            self.sys_msg(str(e), "red")
            return
        await self._connect_server(onion, invite=invite, local_name=local)

    async def open_server(self, name: str) -> None:
        """/server <saved-name> — reconnect using the stored token."""
        servers = store.load_servers()
        if not name:
            if not servers:
                self.sys_msg("no saved servers — /joinserver <onion> <invite>")
                return
            for n, s in sorted(servers.items()):
                t = Text("  ")
                t.append(f"{n:<20}", style="bold cyan")
                t.append(s.get("onion", "")[:24] + "…", style="bright_black")
                self.chat.write(t)
            return
        entry = servers.get(name)
        if not entry:
            self.sys_msg(f"no saved server “{name}” — /server lists them", "red")
            return
        await self._connect_server(entry["onion"], token=entry.get("token", ""),
                                   local_name=name)

    async def _connect_server(self, onion: str, invite: str = "", token: str = "",
                              local_name: str = "") -> None:
        if self.session is not None:
            self.sys_msg("already in a session — /disconnect first", "red")
            return
        self.sys_msg(f"connecting to server {onion[:16]}… (can take ~15s)")
        try:
            reader, writer = await self.tor.dial(onion)
            session = await proto.handshake(reader, writer)
        except Exception as e:
            self.sys_msg(f"connection failed: {e}", "red")
            return
        try:
            hello = await asyncio.wait_for(session.recv(), timeout=HANDSHAKE_TIMEOUT)
        except Exception as e:
            await session.close()
            self.sys_msg(f"no response from server: {e}", "red")
            return
        if hello.get("t") != "srvhello":
            await session.close()
            self.sys_msg("that address is a person, not a server — use /connect", "red")
            return

        self.session = session
        self.state = SERVER
        self.srv = {
            "onion": onion, "name": str(hello.get("name", "server"))[:40],
            "local": local_name, "channel": "", "channels": [], "admin": False,
            "online": [], "buffers": {}, "download": None,
        }
        await session.send({"t": "auth", "nick": self.nick,
                            "invite": invite, "token": token})
        self.run_worker(self.server_loop(session), exclusive=False)

    async def server_loop(self, session: proto.Session) -> None:
        try:
            while True:
                msg = await session.recv()
                await self.handle_server_msg(msg)
        except (asyncio.IncompleteReadError, ConnectionError):
            if self.session is session:
                self.sys_msg("disconnected from server", "red")
        except Exception as e:
            if self.session is session:
                self.sys_msg(f"server session error: {e}", "red")
        finally:
            if self.session is session:
                await self.drop_session()

    # ---------- incoming ----------

    async def handle_server_msg(self, msg: dict) -> None:
        kind = msg["t"]
        if kind == "authok":
            self.srv_authok(msg)
        elif kind == "event":
            self.srv_event(msg)
        elif kind == "histbatch":
            self.srv_histbatch(msg)
        elif kind == "members":
            self.srv["channels"] = [str(c) for c in msg.get("channels", [])]
            self.srv["online"] = [str(n) for n in msg.get("online", [])]
            self.refresh_sidebar()
        elif kind == "srverr":
            self.sys_msg(str(msg.get("msg", ""))[:400], "yellow")
        elif kind == "mget":
            self.srv_mget(msg)
        elif kind == "mgchunk":
            await self.srv_mgchunk(msg)

    def srv_authok(self, msg: dict) -> None:
        s = self.srv
        s["name"] = str(msg.get("server", s["name"]))[:40]
        s["admin"] = bool(msg.get("admin"))
        s["channels"] = [str(c) for c in msg.get("channels", [])]
        s["channel"] = str(msg.get("channel", "general"))
        server_nick = str(msg.get("nick", self.nick))[:32]
        token = msg.get("token")

        local = s["local"] or "".join(
            ch for ch in s["name"].lower().replace(" ", "-") if ch.isalnum() or ch in "-_"
        )[:32] or "server"
        s["local"] = local
        try:
            store.save_server(local, s["onion"], token or "", s["name"])
        except ValueError:
            pass

        self.sys_msg(f"joined “{s['name']}” as {server_nick}"
                     + (" (admin)" if s["admin"] else ""), "green")
        if token:
            self.sys_msg(f"membership saved — reconnect later with /server {local}",
                         "green")
        self.sys_msg("note: a server operator can read channel messages "
                     "(DMs stay end-to-end)", "bright_black")
        self.show_sidebar(True)
        self.refresh_sidebar()
        self.update_server_status()

    def srv_histbatch(self, msg: dict) -> None:
        chan = str(msg.get("chan", ""))
        buf = self.srv["buffers"].setdefault(chan, [])
        buf.clear()
        for m in msg.get("msgs", [])[-200:]:
            buf.append(self.render_event(m, historic=True))
        if chan == self.srv["channel"]:
            self.redraw_channel()

    def srv_event(self, msg: dict) -> None:
        chan = str(msg.get("chan", ""))
        line = self.render_event(msg)
        self.srv["buffers"].setdefault(chan, []).append(line)
        if chan == self.srv["channel"]:
            self.chat.write(line)
            inline = msg.get("inline")
            if inline:
                self.render_inline_image(msg, inline)
        elif str(msg.get("nick")) != self.nick:
            self.sys_msg(f"new message in #{chan}", "bright_black")

    def render_event(self, m: dict, historic: bool = False) -> Text:
        ts = time.strftime("%H:%M", time.localtime(m.get("ts", time.time())))
        nick = str(m.get("nick", "?"))[:32]
        t = Text()
        t.append(f"{ts} ", style="bright_black")
        t.append(f"{nick} ", style="bold cyan" if nick == self.nick else "bold magenta")
        t.append("▸ ", style="bright_black")
        media = m.get("media")
        if media:
            size = media.get("size", 0)
            if media.get("kind") == "img":
                t.append(f"[image · {size // 1024} KB]", style="magenta")
            else:
                t.append(f"[video · {size / 1024 / 1024:.1f} MB] "
                         f"download with /get {media.get('id')}", style="magenta")
        else:
            t.append(str(m.get("body", "")))
        return t

    def render_inline_image(self, msg: dict, inline: str) -> None:
        try:
            data = base64.b64decode(inline, validate=True)
            if len(data) > proto.MAX_IMAGE_BYTES:
                raise ValueError("too large")
            fmt = validate_image(data)
        except Exception as e:
            self.sys_msg(f"could not display image: {e}", "red")
            return
        dest = self._next_received("img", fmt)
        dest.write_bytes(data)
        try:
            self.chat.write(render_preview(data))
        except Exception:
            pass
        self.sys_msg(f"saved → {dest.name}", "bright_black")

    # ---------- media download ----------

    def srv_mget(self, msg: dict) -> None:
        try:
            size = int(msg.get("size", 0))
            chunks = int(msg.get("chunks", 0))
            if not (0 < size <= proto.MAX_BIG_VIDEO_BYTES):
                raise ValueError("bad size")
            if not media.plausible_chunk_count(size, chunks):
                raise ValueError("bad chunk count")
            RECEIVED_DIR.mkdir(exist_ok=True)
            if not media.room_for(RECEIVED_DIR, size):
                raise ValueError(f"not enough free disk space for {fmt_size(size)}")
        except (TypeError, ValueError) as e:
            self.sys_msg(f"rejected download: {e}", "red")
            return
        ext = "".join(ch for ch in str(msg.get("ext", "mp4"))[:5] if ch.isalnum()) or "mp4"
        old = self.srv.get("download")
        if old:
            old["sink"].abort()
        sink = media.ChunkSink(size, chunks, str(msg.get("sha256", ""))[:64],
                               ext=ext, tmp_dir=RECEIVED_DIR)
        self.srv["download"] = {"sink": sink, "kind": str(msg.get("kind", "vid")),
                                "ext": ext}
        self.sys_msg(f"downloading {fmt_size(size)}…", "magenta")

    async def srv_mgchunk(self, msg: dict) -> None:
        dl = self.srv.get("download")
        if dl is None:
            return
        sink = dl["sink"]
        try:
            sink.write(base64.b64decode(msg.get("data", ""), validate=True))
        except Exception as e:
            sink.abort()
            self.srv["download"] = None
            self.sys_msg(f"download failed: {e}", "red")
            self.update_server_status()
            return
        self.update_server_status(f"downloading {sink.progress}%")
        if not sink.complete:
            return

        self.srv["download"] = None
        dest = self._next_received("img" if dl["kind"] == "img" else "vid", dl["ext"])
        try:
            await asyncio.to_thread(sink.finish, dest)
        except (ValueError, OSError) as e:
            self.sys_msg(f"download failed: {e}", "red")
            self.update_server_status()
            return
        if dl["kind"] == "vid" and video.have_ffmpeg():
            try:
                await asyncio.to_thread(video.validate_received, dest)
            except Exception as e:
                dest.unlink(missing_ok=True)
                self.sys_msg(f"rejected download: {e}", "red")
                self.update_server_status()
                return
        self.sys_msg(f"downloaded → {dest}", "green")
        self.update_server_status()

    # ---------- outgoing ----------

    def _srv_ready(self) -> bool:
        """The session can vanish between keystrokes; never send into a None."""
        if self.state != SERVER or self.session is None or not self.srv.get("channel"):
            self.sys_msg("not connected to a server", "red")
            return False
        return True

    async def server_post(self, body: str) -> None:
        if not self._srv_ready():
            return
        await self.session.send({"t": "post", "chan": self.srv["channel"],
                                 "body": body})

    async def server_switch(self, chan: str) -> None:
        if not self._srv_ready():
            return
        chan = chan.strip().lstrip("#").lower()
        if not chan:
            self.sys_msg("usage: /ch <channel>", "red")
            return
        if chan not in self.srv["channels"]:
            self.sys_msg(f"no channel #{chan} (see the sidebar)", "red")
            return
        if chan == self.srv.get("channel"):
            return
        self.srv["channel"] = chan
        await self.session.send({"t": "switch", "chan": chan})
        self.redraw_channel()
        self.mark_active_channel()
        self.update_server_status()

    async def server_send_media(self, path_str: str, kind: str,
                                big: bool = False) -> None:
        if not self._srv_ready():
            return
        path = Path(path_str).expanduser()
        if not path.is_file():
            self.sys_msg(f"no such file: {path}", "red")
            return
        if kind == "img":
            blob = path.read_bytes()
            if len(blob) > proto.MAX_IMAGE_BYTES:
                self.sys_msg("image too large (5 MB max)", "red")
                return
            try:
                ext = validate_image(blob)
            except Exception as e:
                self.sys_msg(f"not a supported image: {e}", "red")
                return
            payload, tmpdir = path, None
        else:
            if not video.have_ffmpeg():
                self.sys_msg("ffmpeg is required to send video", "red")
                return
            prepared = await self.prepare_video(path, big)
            if prepared is None:
                self.update_server_status()
                return
            payload, tmpdir = prepared
            ext = "mp4"

        try:
            if self.state != SERVER or self.session is None:
                return
            size = payload.stat().st_size
            if kind == "vid":
                self.sys_msg(f"uploading {fmt_size(size)} "
                             "(tor is slow — this can take a while)")
            sha = await asyncio.to_thread(media.sha256_file, payload)
            await media.send_file(
                self.session, payload,
                {"t": "mput", "kind": kind, "ext": ext, "chan": self.srv["channel"]},
                "mchunk", proto.chunk_size_for(size), sha,
                on_progress=lambda sent, total: self.update_server_status(
                    f"uploading {sent * 100 // total}%"),
                keep_going=lambda: self.state == SERVER)
        except ConnectionError:
            self.sys_msg("upload aborted: session ended", "red")
            return
        finally:
            if tmpdir is not None:
                tmpdir.cleanup()
            self.update_server_status()
        self.sys_msg("uploaded ✓ — the server will confirm or reject it", "green")

    async def server_fetch(self, arg: str) -> None:
        if not self._srv_ready():
            return
        try:
            mid = int(arg.strip())
        except ValueError:
            self.sys_msg("usage: /get <id>  (the number shown on a video)", "red")
            return
        await self.session.send({"t": "fetch", "id": mid})

    # ---------- display ----------

    def show_sidebar(self, visible: bool) -> None:
        sidebar = self.query_one("#sidebar")
        sidebar.display = visible

    def refresh_sidebar(self) -> None:
        if self.state != SERVER:
            return
        s = self.srv
        self.query_one("#srvname", Static).update(s["name"][:20])

        listed = [w.channel for w in self.query(ChannelItem)]
        if listed != s["channels"]:
            self.run_worker(self.rebuild_channel_items(), exclusive=True)
        else:
            self.mark_active_channel()

        online = Text("online\n", style="bold")
        for n in s["online"][:12]:
            online.append(f" {n[:18]}\n",
                          style="green" if n == self.nick else "bright_black")
        self.query_one("#onlinelist", Static).update(online)

    async def rebuild_channel_items(self) -> None:
        chanlist = self.query_one("#chanlist")
        await chanlist.remove_children()
        await chanlist.mount_all([ChannelItem(c) for c in self.srv["channels"]])
        self.mark_active_channel()

    def mark_active_channel(self) -> None:
        for w in self.query(ChannelItem):
            w.set_class(w.channel == self.srv.get("channel"), "-active")

    def cycle_channel(self, delta: int) -> None:
        """Arrow-key channel switching, so the input bar keeps focus."""
        if self.state != SERVER:
            return
        chans = self.srv.get("channels") or []
        if len(chans) < 2:
            return
        here = self.srv.get("channel")
        i = chans.index(here) if here in chans else 0
        self.run_worker(self.server_switch(chans[(i + delta) % len(chans)]),
                        exclusive=False)

    def redraw_channel(self) -> None:
        self.chat.clear()
        chan = self.srv["channel"]
        for line in self.srv["buffers"].get(chan, []):
            self.chat.write(line)
        self.chat.write(Text(f"  — #{chan} —", style="bright_black"))

    def update_server_status(self, extra: str = "") -> None:
        s = self.srv
        right = f"#{s['channel']} · {len(s['online'])} online"
        if extra:
            right += f" · {extra}"
        self.status_line = f" {s['name']}  ·  {right}"
        self.query_one("#status").update(self.status_line)

    # ---------- admin ----------

    async def server_admin(self, cmd: str, arg: str) -> None:
        if not self._srv_ready():
            return
        if cmd == "/mkchan":
            await self.session.send({"t": "mkchan", "name": arg.strip().lstrip("#").lower()})
        elif cmd == "/rmchan":
            await self.session.send({"t": "rmchan", "name": arg.strip().lstrip("#").lower()})
        elif cmd == "/newinvite":
            parts = arg.split()
            uses = 1
            admin = "admin" in parts
            for p in parts:
                if p.isdigit():
                    uses = int(p)
            await self.session.send({"t": "newinvite", "uses": uses, "admin": admin})
        elif cmd == "/kick":
            if not arg.strip():
                self.sys_msg("usage: /kick <nick>", "red")
                return
            await self.session.send({"t": "kick", "nick": arg.strip()})
