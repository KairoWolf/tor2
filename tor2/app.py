"""tor2 terminal UI."""

import asyncio
import base64
import getpass
import hashlib
import re
import secrets
import tempfile
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, RichLog, Static

from . import media, proto, store, video
from .app_consts import RECEIVED_DIR, fmt_size
from .clientserver import SERVER, ChannelItem, ServerModeMixin
from .imgview import is_animated, render_frames, render_preview, validate_image
from . import updater
from .tornet import TorNet, code_to_key, normalize_onion, onion_from_pub

CODE_TTL_S = 15 * 60

IDLE, PENDING_IN, PENDING_OUT, CONNECTED = "idle", "pending_in", "pending_out", "connected"


def clean_nick(raw: str) -> str:
    return re.sub(r"[^\w\- ]", "", str(raw))[:32].strip()


class Tor2App(ServerModeMixin, App):
    TITLE = "tor2"

    CSS = """
    #status {
        dock: top;
        height: 1;
        padding: 0 1;
        background: $primary;
        color: $text;
        text-style: bold;
    }
    #sidebar {
        width: 22;
        border-right: solid $primary-darken-1;
        display: none;
    }
    #srvname { padding: 0 1; text-style: bold; }
    #chanlist { height: auto; }
    #onlinelist { padding: 1 1 0 1; }
    .chan {
        padding: 0 1;
        color: $text-muted;
    }
    .chan:hover { background: $boost; }
    .chan.-active {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    #chat {
        border: round $primary-darken-1;
        padding: 0 1;
        scrollbar-size-vertical: 1;
    }
    #inputbar {
        dock: bottom;
        border: round $accent;
    }
    #progress {
        dock: bottom;
        height: 1;
        padding: 0 1;
        color: $success;
        display: none;
    }
    #preview {
        dock: bottom;
        height: auto;
        max-height: 26;
        padding: 0 1;
        border: round $success;
        display: none;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("up", "channel_prev", "Previous channel"),
        ("down", "channel_next", "Next channel"),
    ]

    def __init__(self):
        super().__init__()
        cfg = store.load_config()
        self.nick = clean_nick(cfg.get("nick", "")) or getpass.getuser()
        self.tor = TorNet(Path.cwd() / ".tordata")
        self.session: proto.Session | None = None
        self.state = IDLE
        self.peer_nick = "peer"
        self.last_onion: str | None = None       # last address we dialed
        self._server: asyncio.Server | None = None
        self._incoming_video: dict | None = None  # in-progress receive
        self.pairing_code: str | None = None
        self._code_timer = None
        self.srv: dict = {}
        self.cache = media.MediaCache()
        self._anim_timer = None
        self._anim: dict | None = None
        self.last_rx = 0.0
        self.manual_disconnect = False

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        yield Static(" tor2 · starting…", id="status")
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static(id="srvname")
                yield Vertical(id="chanlist")
                yield Static(id="onlinelist")
            yield RichLog(id="chat", wrap=True, markup=False)
        yield Static(id="preview")
        yield Static(id="progress")
        yield Input(placeholder="message…  (/help for commands)", id="inputbar")

    # ---------- helpers ----------

    @property
    def chat(self) -> RichLog:
        return self.query_one("#chat", RichLog)

    def set_status(self, right: str) -> None:
        addr = self.tor.onion_addr or "starting…"
        self.status_line = f" {addr}  ·  {right}"
        self.query_one("#status", Static).update(self.status_line)

    def sys_msg(self, msg: str, style: str = "bright_black") -> None:
        self.chat.write(Text(f"  • {msg}", style=style))

    # ---------- progress ----------

    def progress(self, label: str, fraction: float) -> None:
        """Show a labelled bar above the input bar. fraction >= 1 finishes it."""
        bar = self.query_one("#progress", Static)
        if fraction >= 1.0:
            bar.display = False
            return
        width = 28
        filled = max(0, min(width, round(fraction * width)))
        meter = "█" * filled + "░" * (width - filled)
        bar.update(f"{label} ▕{meter}▏ {fraction * 100:3.0f}%")
        bar.display = True

    def progress_done(self) -> None:
        self.query_one("#progress", Static).display = False

    # ---------- previews ----------

    def show_preview(self, data: bytes, label: str = "") -> None:
        """Draw an image in the chat log; animate it in the pane if it moves."""
        try:
            self.chat.write(render_preview(data))
        except Exception as e:
            self.sys_msg(f"could not render preview: {e}", "red")
            return
        if label:
            self.sys_msg(label, "bright_black")
        if is_animated(data):
            self.sys_msg("animated — playing below (any new preview replaces it)",
                         "bright_black")
            self.run_worker(self.animate(data), exclusive=False)

    async def animate(self, data: bytes) -> None:
        try:
            frames, delay = await asyncio.to_thread(render_frames, data)
        except Exception as e:
            self.sys_msg(f"could not animate: {e}", "red")
            return
        if len(frames) < 2:
            return
        self.stop_animation()
        pane = self.query_one("#preview", Static)
        pane.display = True
        self._anim = {"frames": frames, "i": 0, "loops": 0, "max_loops": 3}
        pane.update(frames[0])
        self._anim_timer = self.set_interval(delay, self._next_frame)

    def _next_frame(self) -> None:
        a = self._anim
        if not a:
            return
        a["i"] += 1
        if a["i"] >= len(a["frames"]):
            a["i"] = 0
            a["loops"] += 1
            if a["loops"] >= a["max_loops"]:
                self.stop_animation()
                return
        self.query_one("#preview", Static).update(a["frames"][a["i"]])

    def stop_animation(self) -> None:
        if self._anim_timer is not None:
            self._anim_timer.stop()
            self._anim_timer = None
        self._anim = None
        try:
            self.query_one("#preview", Static).display = False
        except Exception:
            pass

    async def cmd_play(self, arg: str) -> None:
        """/play [id] — replay an animation from the preview cache."""
        key = arg.strip() or self.cache.last_key
        if not key:
            self.sys_msg("nothing to play yet", "red")
            return
        got = self.cache.get(key)
        if got is None:
            self.sys_msg(f"image {key} is not in the preview cache — "
                         f"/get {key} downloads it", "red")
            return
        await self.animate(got[0])

    def cmd_save(self, arg: str) -> None:
        """/save [id] — write a previewed image to ./received."""
        key = arg.strip() or self.cache.last_key
        if not key:
            self.sys_msg("nothing to save yet", "red")
            return
        got = self.cache.get(key)
        if got is None:
            self.sys_msg(f"image {key} is not in the preview cache", "red")
            return
        data, ext = got
        dest = self._next_received("img", ext)
        dest.write_bytes(data)
        self.sys_msg(f"saved → {dest}", "green")

    # ---------- keepalive ----------

    async def keepalive(self, session: proto.Session) -> None:
        """Ping periodically and give up if the peer goes quiet.

        Without this an idle Tor stream can be dropped by a relay or NAT with
        no notification, and the session only fails on the next real message.
        """
        while self.session is session:
            await asyncio.sleep(proto.KEEPALIVE_INTERVAL)
            if self.session is not session:
                return
            quiet = asyncio.get_event_loop().time() - self.last_rx
            if quiet > proto.KEEPALIVE_TIMEOUT:
                self.sys_msg("connection timed out (no reply from the other end)",
                             "red")
                await self.drop_session()
                return
            try:
                await session.send({"t": "ping"})
            except Exception:
                return

    def touch_rx(self) -> None:
        self.last_rx = asyncio.get_event_loop().time()

    # ---------- updates ----------

    async def cmd_update(self, arg: str) -> None:
        """/update — fast-forward this checkout to the latest release."""
        check_only = arg.strip() in ("check", "--check")
        self.sys_msg("checking github for a newer version…")
        try:
            info = await asyncio.to_thread(updater.check)
        except updater.UpdateError as e:
            self.sys_msg(f"update check failed: {e}", "red")
            return
        if not info["behind"]:
            self.sys_msg(f"you are up to date ({info['current']})", "green")
            return
        self.sys_msg(f"{info['behind']} new commit(s) available "
                     f"({info['current']} → {info['latest']}):", "yellow")
        for line in info["summary"].splitlines()[:5]:
            self.sys_msg(f"    {line}", "bright_black")
        if check_only:
            self.sys_msg("run /update to apply")
            return
        try:
            await asyncio.to_thread(updater.apply)
        except updater.UpdateError as e:
            self.sys_msg(f"update failed: {e}", "red")
            return
        self.sys_msg("updated ✓ — quit (ctrl+q) and start tor2 again to run it",
                     "green")
        self.sys_msg("if dependencies changed, also run: "
                     ".venv/bin/pip install -r requirements.txt", "bright_black")

    def chat_msg(self, who: str, body: str, mine: bool) -> None:
        t = Text()
        t.append(f"{who} ", style="bold cyan" if mine else "bold magenta")
        t.append("▸ ", style="bright_black")
        t.append(body)
        self.chat.write(t)

    # ---------- startup ----------

    def on_mount(self) -> None:
        self.status_line = ""
        self.query_one("#inputbar", Input).focus()
        self.sys_msg(f"welcome, {self.nick} — for lawful use only (/help for commands)")
        self.run_worker(self.start_network(), group="net", exclusive=False)

    async def start_network(self) -> None:
        self.sys_msg("bootstrapping tor (first run can take ~60s)…")

        def progress(pct: int) -> None:
            self.call_from_thread(
                self.query_one("#status", Static).update,
                f" tor2 · tor bootstrap {pct}%")

        try:
            await asyncio.to_thread(self.tor.launch, progress)
        except Exception as e:
            self.sys_msg(f"failed to start tor: {e}", "red")
            return

        self._server = await asyncio.start_server(self.on_incoming, "127.0.0.1", 0)
        local_port = self._server.sockets[0].getsockname()[1]

        self.query_one("#status", Static).update(" tor2 · publishing onion service…")
        try:
            await asyncio.to_thread(self.tor.create_onion, local_port)
        except Exception as e:
            self.sys_msg(f"failed to publish onion service: {e}", "red")
            return

        self.set_status("not connected")
        self.sys_msg(f"your address: {self.tor.onion_addr}", "green")
        self.sys_msg("share it with your peer, or /connect <contact-or-onion>")
        if not video.have_ffmpeg():
            self.sys_msg("ffmpeg not found — /vid disabled until it is installed", "yellow")

    # ---------- connection handling ----------

    async def on_incoming(self, reader, writer) -> None:
        if self.session is not None:
            writer.close()
            return
        try:
            session = await proto.handshake(reader, writer)
            hello = await asyncio.wait_for(session.recv(), timeout=60)
            if hello.get("t") != "hello":
                raise proto.ProtocolError("expected hello")
        except Exception as e:
            self.sys_msg(f"incoming connection failed handshake: {e}", "red")
            writer.close()
            return
        if self.session is not None:
            await session.close()
            return
        self.session = session
        self.state = PENDING_IN
        self.peer_nick = clean_nick(hello.get("nick", "")) or "peer"
        self.set_status(f"incoming request from “{self.peer_nick}”")
        self.sys_msg(f"incoming chat request from “{self.peer_nick}”", "yellow")
        self.sys_msg(f"session fingerprint: {session.fingerprint}", "yellow")
        self.sys_msg("type /accept to start chatting, or /reject to refuse", "yellow")
        self.run_worker(self.recv_loop(session), group="net", exclusive=False)

    async def do_accept(self) -> None:
        if self.state != PENDING_IN or self.session is None:
            self.sys_msg("no pending chat request", "red")
            return
        await self.session.send({"t": "accept", "nick": self.nick})
        self.state = CONNECTED
        self.on_connected()

    async def do_reject(self) -> None:
        if self.state != PENDING_IN or self.session is None:
            self.sys_msg("no pending chat request", "red")
            return
        self.sys_msg(f"rejected chat request from “{self.peer_nick}”")
        await self.drop_session()

    async def do_connect(self, arg: str) -> None:
        if self.session is not None:
            self.sys_msg("already in a session — /disconnect first", "red")
            return
        contacts = store.load_contacts()
        target, label = arg, arg
        if arg in contacts:
            target, label = contacts[arg], f"{arg} ({contacts[arg][:12]}…)"
        try:
            onion = normalize_onion(target)
        except ValueError as e:
            self.sys_msg(f"{e} — not a contact name either (see /contacts)", "red")
            return
        self.sys_msg(f"connecting to {label} (can take ~10s)…")
        try:
            reader, writer = await self.tor.dial(onion)
            session = await proto.handshake(reader, writer)
        except Exception as e:
            self.sys_msg(f"connection failed: {e}", "red")
            return
        if self.session is not None:
            await session.close()
            return
        self.session = session
        self.state = PENDING_OUT
        self.last_onion = onion
        await session.send({"t": "hello", "nick": self.nick})
        self.set_status("waiting for peer to accept…")
        self.sys_msg("connected — waiting for your peer to accept the chat")
        self.sys_msg(f"session fingerprint: {session.fingerprint}", "green")
        self.run_worker(self.recv_loop(session), group="net", exclusive=False)

    # ---------- pairing codes ----------

    async def do_code(self, arg: str) -> None:
        if arg == "off":
            await self.stop_code()
            self.sys_msg("pairing code disabled")
            return
        if not self.tor.onion_addr or self._server is None:
            self.sys_msg("tor is not ready yet", "red")
            return
        if self.pairing_code:
            self.sys_msg(f"pairing code already active: {self.pairing_code}", "yellow")
            return
        code = f"{secrets.randbelow(100000):05d}"
        local_port = self._server.sockets[0].getsockname()[1]
        self.sys_msg("publishing pairing code (takes a few seconds)…")
        try:
            await asyncio.to_thread(self.tor.publish_code_onion, code, local_port)
        except Exception as e:
            self.sys_msg(f"failed to publish pairing code: {e}", "red")
            return
        self.pairing_code = code
        self._code_timer = self.set_timer(CODE_TTL_S, self._expire_code)
        self.sys_msg(f"pairing code: {code}", "green")
        self.sys_msg(f"your peer types: /join {code}   (valid 15 min, single use)")
        self.sys_msg("anyone guessing the code can *request* a chat — "
                     "you still /accept and verify the fingerprint")

    async def do_join(self, code: str) -> None:
        if not re.fullmatch(r"\d{5}", code):
            self.sys_msg("usage: /join <5-digit code>", "red")
            return
        _, pub = code_to_key(code)
        await self.do_connect(onion_from_pub(pub))

    async def stop_code(self) -> None:
        if self._code_timer:
            self._code_timer.stop()
            self._code_timer = None
        if self.pairing_code:
            self.pairing_code = None
            await asyncio.to_thread(self.tor.remove_code_onion)

    async def _expire_code(self) -> None:
        if self.pairing_code:
            self.sys_msg("pairing code expired", "yellow")
            await self.stop_code()

    def on_connected(self) -> None:
        if self.pairing_code:  # single use: retire the rendezvous address
            self.run_worker(self.stop_code(), exclusive=False)
        self.set_status(f"connected to “{self.peer_nick}” ✓")
        self.sys_msg(f"secure session with “{self.peer_nick}” — say hi!", "green")
        self.sys_msg("verify the fingerprint matches on both screens", "green")
        if self.last_onion and self.last_onion not in store.load_contacts().values():
            self.sys_msg(f"save this peer with: /add <name>", "bright_black")

    async def drop_session(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
        was_server = self.state == SERVER
        self.state = IDLE
        self.peer_nick = "peer"
        self._incoming_video = None
        if was_server:
            self.srv = {}
            self.show_sidebar(False)
            self.chat.clear()
        if self.tor.onion_addr:
            self.set_status("not connected")

    # ---------- receiving ----------

    async def recv_loop(self, session: proto.Session) -> None:
        self.touch_rx()
        self.run_worker(self.keepalive(session), group="net", exclusive=False)
        try:
            while True:
                msg = await session.recv()
                self.touch_rx()
                if msg["t"] == "ping":
                    await session.send({"t": "pong"})
                    continue
                if msg["t"] == "pong":
                    continue
                await self.handle_message(msg)
        except (asyncio.IncompleteReadError, ConnectionError):
            if self.session is session:
                self.sys_msg("peer disconnected", "red")
        except Exception as e:
            if self.session is session:
                self.sys_msg(f"session error: {e}", "red")
        finally:
            if self.session is session:
                await self.drop_session()

    async def handle_message(self, msg: dict) -> None:
        kind = msg["t"]
        if kind == "srvhello":
            name = str(msg.get("name", "server"))[:40]
            self.sys_msg(f"“{name}” is a tor2 server, not a person — join it with "
                         "/joinserver <onion> <invite>", "yellow")
            await self.drop_session()
            return
        if kind == "accept":
            if self.state == PENDING_OUT:
                self.peer_nick = clean_nick(msg.get("nick", "")) or "peer"
                self.state = CONNECTED
                self.on_connected()
            return
        if self.state != CONNECTED:
            return  # nothing else is processed before the chat is accepted
        if kind == "txt":
            self.chat_msg(self.peer_nick, str(msg.get("body", ""))[:10000], mine=False)
        elif kind == "img":
            self.handle_image(msg)
        elif kind == "vmeta":
            self.handle_video_meta(msg)
        elif kind == "vchunk":
            await self.handle_video_chunk(msg)

    def handle_image(self, msg: dict) -> None:
        try:
            data = base64.b64decode(msg.get("data", ""), validate=True)
            if len(data) > proto.MAX_IMAGE_BYTES:
                raise ValueError("image exceeds size limit")
            fmt = validate_image(data)
        except Exception as e:
            self.sys_msg(f"rejected incoming image: {e}", "red")
            return
        # Previewed, not written to disk: /save decides what is kept.
        key = f"dm{len(self.cache._items) + 1}"
        self.cache.put(key, data, fmt)
        self.sys_msg(f"{self.peer_nick} sent an image ({fmt_size(len(data))})",
                     "magenta")
        self.show_preview(data, "/save writes it to ./received")

    def handle_video_meta(self, msg: dict) -> None:
        try:
            size = int(msg.get("size", 0))
            chunks = int(msg.get("chunks", 0))
            sha = str(msg.get("sha256", ""))
            if not (0 < size <= proto.MAX_BIG_VIDEO_BYTES):
                raise ValueError("bad size")
            if not media.plausible_chunk_count(size, chunks):
                raise ValueError("bad chunk count")
            RECEIVED_DIR.mkdir(exist_ok=True)
            if not media.room_for(RECEIVED_DIR, size):
                raise ValueError(f"not enough free disk space for {fmt_size(size)}")
        except ValueError as e:
            self.sys_msg(f"rejected incoming video: {e}", "red")
            return
        if self._incoming_video is not None:
            self._incoming_video.abort()
        # Streams to a temp file: a multi-gigabyte video never sits in memory.
        self._incoming_video = media.ChunkSink(size, chunks, sha, ext="mp4",
                                               tmp_dir=RECEIVED_DIR)
        self.sys_msg(f"{self.peer_nick} is sending a video ({fmt_size(size)})…",
                     "magenta")

    async def handle_video_chunk(self, msg: dict) -> None:
        sink = self._incoming_video
        if sink is None:
            return
        try:
            sink.write(base64.b64decode(msg.get("data", ""), validate=True))
        except Exception as e:
            sink.abort()
            self._incoming_video = None
            self.sys_msg(f"rejected video: {e}", "red")
            self.set_status(f"connected to “{self.peer_nick}” ✓")
            return
        self.progress(f"receiving from {self.peer_nick}", sink.got / sink.size)
        if not sink.complete:
            return

        self._incoming_video = None
        self.progress_done()
        dest = self._next_received("vid", "mp4")
        try:
            await asyncio.to_thread(sink.finish, dest)
        except (ValueError, OSError) as e:
            self.sys_msg(f"rejected video: {e}", "red")
            self.set_status(f"connected to “{self.peer_nick}” ✓")
            return
        if video.have_ffmpeg():
            try:
                await asyncio.to_thread(video.validate_received, dest)
            except Exception as e:
                dest.unlink(missing_ok=True)
                self.sys_msg(f"rejected video: {e}", "red")
                self.set_status(f"connected to “{self.peer_nick}” ✓")
                return
        else:
            self.sys_msg("note: ffmpeg not installed, saved without verifying "
                         "it decodes as video", "yellow")
        self.sys_msg(f"{self.peer_nick} sent a video ({fmt_size(sink.size)}) → {dest}",
                     "magenta")
        await self.preview_video(dest)
        self.set_status(f"connected to “{self.peer_nick}” ✓")

    @staticmethod
    def _next_received(prefix: str, ext: str) -> Path:
        RECEIVED_DIR.mkdir(exist_ok=True)
        n = 1
        while (dest := RECEIVED_DIR / f"{prefix}_{n:03d}.{ext}").exists():
            n += 1
        return dest

    # ---------- sending ----------

    def _require_connected(self) -> bool:
        if self.state != CONNECTED or self.session is None:
            self.sys_msg("not in an accepted session — /connect first", "red")
            return False
        return True

    async def send_text(self, body: str) -> None:
        if not self._require_connected():
            return
        await self.session.send({"t": "txt", "body": body})
        self.chat_msg(self.nick, body, mine=True)

    async def send_image(self, path_str: str) -> None:
        if not self._require_connected():
            return
        path = Path(path_str).expanduser()
        if not path.is_file():
            self.sys_msg(f"no such file: {path}", "red")
            return
        data = path.read_bytes()
        if len(data) > proto.MAX_IMAGE_BYTES:
            self.sys_msg("image too large (5 MB max) — try /vid for videos", "red")
            return
        try:
            validate_image(data)
        except Exception as e:
            self.sys_msg(f"not a supported image: {e}", "red")
            return
        await self.session.send({"t": "img", "data": base64.b64encode(data).decode()})
        self.chat.write(render_preview(data))
        self.sys_msg(f"image sent ({len(data) // 1024} KB)", "green")

    async def send_video(self, path_str: str, big: bool = False) -> None:
        if not self._require_connected():
            return
        if not video.have_ffmpeg():
            self.sys_msg("ffmpeg is required for video — install it first", "red")
            return
        src = Path(path_str).expanduser()
        if not src.is_file():
            self.sys_msg(f"no such file: {src}", "red")
            return
        prepared = await self.prepare_video(src, big)
        if prepared is None:
            return
        payload, tmpdir = prepared
        try:
            if not self._require_connected():
                return
            size = payload.stat().st_size
            self.sys_msg(f"sending video: {fmt_size(size)} "
                         f"(tor is slow — this can take a while)")
            sha = await asyncio.to_thread(media.sha256_file, payload)
            await media.send_file(
                self.session, payload, {"t": "vmeta"}, "vchunk",
                proto.chunk_size_for(size), sha,
                on_progress=lambda sent, total: self.progress(
                    "sending video", sent / total),
                keep_going=lambda: self.state == CONNECTED)
        except ConnectionError:
            self.sys_msg("video send aborted: session ended", "red")
            return
        finally:
            self.progress_done()
            tmpdir.cleanup()
            if self.state == CONNECTED:
                self.set_status(f"connected to “{self.peer_nick}” ✓")
        self.sys_msg("video sent ✓", "green")

    async def preview_video(self, path: Path) -> None:
        """Show a still from a video file, so you can see what arrived."""
        thumb = await asyncio.to_thread(video.thumbnail, path)
        if not thumb:
            return
        try:
            self.chat.write(render_preview(thumb, width=44))
        except Exception:
            pass

    async def prepare_video(self, src: Path, big: bool):
        """Probe and compress. Returns (payload_path, tmpdir) or None."""
        limit = video.MAX_BIG_SOURCE_BYTES if big else video.MAX_SOURCE_BYTES
        if src.stat().st_size > limit:
            self.sys_msg(f"source video too large (max {fmt_size(limit)})", "red")
            return None
        try:
            info = await asyncio.to_thread(video.probe, src)
        except Exception as e:
            self.sys_msg(f"not a readable video: {e}", "red")
            return None
        if not big and info["duration"] > video.MAX_DURATION_S:
            self.sys_msg("video too long (10 min max) — use /big-vid instead", "red")
            return None

        mins = info["duration"] / 60
        self.sys_msg(f"compressing {src.name} ({mins:.1f} min)"
                     + (" — this can take a while for long videos…" if big else "…"))
        tmpdir = tempfile.TemporaryDirectory(prefix="tor2vid-")
        out = Path(tmpdir.name) / "out.mp4"

        def on_progress(fraction: float) -> None:
            self.call_from_thread(self.progress, "compressing", fraction)

        try:
            await asyncio.to_thread(video.compress, src, out, on_progress,
                                    info["duration"])
        except Exception as e:
            self.progress_done()
            self.sys_msg(str(e), "red")
            tmpdir.cleanup()
            return None
        self.progress_done()

        size = out.stat().st_size
        cap = proto.MAX_BIG_VIDEO_BYTES if big else proto.MAX_VIDEO_BYTES
        if size > cap:
            self.sys_msg(
                f"still {fmt_size(size)} after compression — over the "
                f"{fmt_size(cap)} limit", "red")
            tmpdir.cleanup()
            return None
        saved = 100 - round(size * 100 / max(1, src.stat().st_size))
        self.sys_msg(f"compressed {fmt_size(src.stat().st_size)} → "
                     f"{fmt_size(size)} ({saved}% smaller)", "green")
        return out, tmpdir

    # ---------- input ----------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        event.input.value = ""
        if not line:
            return
        if line.startswith("/"):
            await self.handle_command(line)
        elif self.state == SERVER:
            await self.server_post(line)
        else:
            await self.send_text(line)

    async def handle_command(self, line: str) -> None:
        cmd, _, arg = line.partition(" ")
        arg = arg.strip()

        if cmd == "/update":
            self.run_worker(self.cmd_update(arg), exclusive=False)
            return
        if cmd in ("/joinserver", "/server"):
            coro = self.join_server(arg) if cmd == "/joinserver" else self.open_server(arg)
            self.run_worker(coro, exclusive=False)
            return
        if self.state == SERVER:
            if await self.handle_server_command(cmd, arg):
                return

        match cmd:
            case "/connect":
                self.run_worker(self.do_connect(arg), exclusive=False)
            case "/code":
                self.run_worker(self.do_code(arg), exclusive=False)
            case "/join":
                self.run_worker(self.do_join(arg), exclusive=False)
            case "/accept":
                await self.do_accept()
            case "/reject":
                await self.do_reject()
            case "/img" | "/image":
                self.run_worker(self.send_image(arg), exclusive=False)
            case "/play":
                await self.cmd_play(arg)
            case "/save":
                self.cmd_save(arg)
            case "/vid" | "/video":
                self.run_worker(self.send_video(arg), exclusive=False)
            case "/big-vid" | "/bigvid":
                self.run_worker(self.send_video(arg, big=True), exclusive=False)
            case "/add":
                self.cmd_add(arg)
            case "/contacts":
                self.cmd_contacts()
            case "/delcontact":
                if store.remove_contact(arg):
                    self.sys_msg(f"removed contact “{arg}”")
                else:
                    self.sys_msg(f"no contact named “{arg}”", "red")
            case "/nick":
                nick = clean_nick(arg)
                if not nick:
                    self.sys_msg("usage: /nick <name>", "red")
                    return
                self.nick = nick
                cfg = store.load_config()
                cfg["nick"] = nick
                store.save_config(cfg)
                self.sys_msg(f"you are now “{self.nick}” (saved; applies to new sessions)")
            case "/disconnect":
                self.manual_disconnect = True
                await self.drop_session()
                self.sys_msg("disconnected")
            case "/help":
                for line_ in (
                    "/code                       — get a 5-digit pairing code to share",
                    "/join <code>                — connect using a peer's pairing code",
                    "/connect <contact-or-onion> — connect to a peer",
                    "/accept · /reject           — answer an incoming chat request",
                    "/img <path>                 — send an image (≤5 MB)",
                    "/save [id] · /play [id]     — keep or replay a previewed image",
                    "/vid <path>                 — send a video (auto-compressed, ≤10 min)",
                    "/big-vid <path>             — send a long video (any length, ≤3 GB)",
                    "/add <name> [onion]         — save a contact (defaults to current peer)",
                    "/contacts · /delcontact <name>",
                    "/nick <name>                — set your display name (persists)",
                    "/update                     — get the latest version from github",
                    "/disconnect · ctrl+q",
                    "— servers —",
                    "/joinserver <onion> <invite> [name] — join a tor2 server",
                    "/server [name]              — reconnect a saved server (or list)",
                ):
                    self.sys_msg(line_)
            case _:
                self.sys_msg(f"unknown command: {cmd} (try /help)", "red")

    async def handle_server_command(self, cmd: str, arg: str) -> bool:
        """Commands that mean something different (or only exist) on a server.
        Returns True if the command was handled here."""
        match cmd:
            case "/ch" | "/channel":
                await self.server_switch(arg)
            case "/channels":
                self.sys_msg("channels: " +
                             " ".join("#" + c for c in self.srv["channels"]))
            case "/members":
                self.sys_msg("online: " + (", ".join(self.srv["online"]) or "(nobody)"))
            case "/img" | "/image":
                self.run_worker(self.server_send_media(arg, "img"), exclusive=False)
            case "/vid" | "/video":
                self.run_worker(self.server_send_media(arg, "vid"), exclusive=False)
            case "/big-vid" | "/bigvid":
                self.run_worker(self.server_send_media(arg, "vid", big=True),
                                exclusive=False)
            case "/get":
                await self.server_fetch(arg)
            case "/more":
                await self.server_more()
            case "/del" | "/delete":
                await self.server_delete(arg)
            case "/play":
                await self.cmd_play(arg)
            case "/save":
                self.cmd_save(arg)
            case "/mkchan" | "/rmchan" | "/newinvite" | "/kick":
                if not self.srv.get("admin"):
                    self.sys_msg("admin only", "red")
                else:
                    await self.server_admin(cmd, arg)
            case "/leave":
                self.manual_disconnect = True
                name = self.srv.get("local", "")
                await self.drop_session()
                if name and store.remove_server(name):
                    self.sys_msg(f"left and forgot server “{name}”")
            case "/help":
                for line_ in (
                    f"— server “{self.srv['name']}” —",
                    "type to chat in the current channel",
                    "click a channel or press ↑/↓ to switch  ·  /ch <name> also works",
                    "/channels · /members",
                    "/img <path>        — post an image (shown inline to everyone)",
                    "/vid <path>        — post a video (others download with /get)",
                    "/big-vid <path>    — post a long video (any length, ≤3 GB)",
                    "/get <id>          — download a posted image or video",
                    "/more              — load older messages",
                    "/del <id>          — delete your message (admins: any)",
                    "/save [id] · /play [id] — keep or replay a previewed image",
                    "/disconnect        — go back to direct-message mode",
                    "/leave             — disconnect and forget this server",
                ):
                    self.sys_msg(line_)
                if self.srv.get("admin"):
                    for line_ in (
                        "admin: /mkchan <name> · /rmchan <name>",
                        "admin: /kick <nick> · /ban <nick> [reason] · /unban <nick> · /bans",
                        "admin: /promote <nick> · /demote <nick>  — grant or remove admin",
                        "admin: /newinvite [uses] [admin]  — mint an invite code",
                        "admin: /autoupdate on|off|now     — auto-update from github",
                    ):
                        self.sys_msg(line_, "cyan")
            case _:
                return False
        return True

    def cmd_add(self, arg: str) -> None:
        name, _, onion = arg.partition(" ")
        onion = onion.strip() or self.last_onion or ""
        if not name or not onion:
            self.sys_msg("usage: /add <name> <onion>  (onion optional after /connect)", "red")
            return
        try:
            onion = normalize_onion(onion)
            store.add_contact(name, onion)
        except ValueError as e:
            self.sys_msg(str(e), "red")
            return
        self.sys_msg(f"saved contact “{name}” → {onion[:20]}…", "green")

    def cmd_contacts(self) -> None:
        contacts = store.load_contacts()
        if not contacts:
            self.sys_msg("no contacts yet — /add <name> <onion>")
            return
        for name, onion in sorted(contacts.items()):
            t = Text("  ")
            t.append(f"{name:<20}", style="bold cyan")
            t.append(onion, style="bright_black")
            self.chat.write(t)

    def action_channel_prev(self) -> None:
        self.cycle_channel(-1)

    def action_channel_next(self) -> None:
        self.cycle_channel(1)

    async def action_quit(self) -> None:
        self.manual_disconnect = True
        await self.stop_code()
        await self.drop_session()
        if self._server:
            self._server.close()
        await asyncio.to_thread(self.tor.shutdown)
        self.exit()


def main() -> None:
    Tor2App().run()
