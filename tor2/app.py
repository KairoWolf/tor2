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
from textual.widgets import Input, RichLog, Static

from . import proto, store, video
from .imgview import render_preview, validate_image
from .tornet import TorNet, code_to_key, normalize_onion, onion_from_pub

CODE_TTL_S = 15 * 60

RECEIVED_DIR = Path.cwd() / "received"

IDLE, PENDING_IN, PENDING_OUT, CONNECTED = "idle", "pending_in", "pending_out", "connected"


def clean_nick(raw: str) -> str:
    return re.sub(r"[^\w\- ]", "", str(raw))[:32].strip()


class Tor2App(App):
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
    #chat {
        border: round $primary-darken-1;
        padding: 0 1;
        scrollbar-size-vertical: 1;
    }
    #inputbar {
        dock: bottom;
        border: round $accent;
    }
    """

    BINDINGS = [("ctrl+q", "quit", "Quit")]

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

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        yield Static(" tor2 · starting…", id="status")
        yield RichLog(id="chat", wrap=True, markup=False)
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
        self.run_worker(self.start_network(), exclusive=False)

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
        self.run_worker(self.recv_loop(session), exclusive=False)

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
        self.run_worker(self.recv_loop(session), exclusive=False)

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
        self.state = IDLE
        self.peer_nick = "peer"
        self._incoming_video = None
        if self.tor.onion_addr:
            self.set_status("not connected")

    # ---------- receiving ----------

    async def recv_loop(self, session: proto.Session) -> None:
        try:
            while True:
                msg = await session.recv()
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
        dest = self._next_received(f"img", fmt)
        dest.write_bytes(data)
        self.sys_msg(f"{self.peer_nick} sent an image → {dest.name}", "magenta")
        try:
            self.chat.write(render_preview(data))
        except Exception:
            self.sys_msg("(no inline preview available)", "yellow")

    def handle_video_meta(self, msg: dict) -> None:
        try:
            size = int(msg.get("size", 0))
            chunks = int(msg.get("chunks", 0))
            sha = str(msg.get("sha256", ""))
            if not (0 < size <= proto.MAX_VIDEO_BYTES):
                raise ValueError("bad size")
            if not (0 < chunks <= size // proto.VIDEO_CHUNK + 1):
                raise ValueError("bad chunk count")
        except ValueError as e:
            self.sys_msg(f"rejected incoming video: {e}", "red")
            return
        self._incoming_video = {"size": size, "chunks": chunks, "sha": sha,
                                "parts": [], "got": 0}
        self.sys_msg(f"{self.peer_nick} is sending a video ({size // 1024 // 1024} MB)…",
                     "magenta")

    async def handle_video_chunk(self, msg: dict) -> None:
        iv = self._incoming_video
        if iv is None:
            return
        try:
            data = base64.b64decode(msg.get("data", ""), validate=True)
        except Exception:
            self.sys_msg("rejected corrupt video chunk", "red")
            self._incoming_video = None
            return
        iv["parts"].append(data)
        iv["got"] += len(data)
        if iv["got"] > iv["size"]:
            self.sys_msg("rejected video: larger than announced", "red")
            self._incoming_video = None
            return
        pct = iv["got"] * 100 // iv["size"]
        self.set_status(f"receiving video from “{self.peer_nick}”… {pct}%")
        if len(iv["parts"]) < iv["chunks"]:
            return
        # complete — verify and save
        self._incoming_video = None
        blob = b"".join(iv["parts"])
        if hashlib.sha256(blob).hexdigest() != iv["sha"]:
            self.sys_msg("rejected video: checksum mismatch", "red")
            self.set_status(f"connected to “{self.peer_nick}” ✓")
            return
        dest = self._next_received("vid", "mp4")
        dest.write_bytes(blob)
        try:
            await asyncio.to_thread(video.validate_received, dest)
        except Exception as e:
            dest.unlink(missing_ok=True)
            self.sys_msg(f"rejected video: {e}", "red")
        else:
            self.sys_msg(f"{self.peer_nick} sent a video → {dest}", "magenta")
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

    async def send_video(self, path_str: str) -> None:
        if not self._require_connected():
            return
        if not video.have_ffmpeg():
            self.sys_msg("ffmpeg is required for video — install it first", "red")
            return
        src = Path(path_str).expanduser()
        if not src.is_file():
            self.sys_msg(f"no such file: {src}", "red")
            return
        if src.stat().st_size > video.MAX_SOURCE_BYTES:
            self.sys_msg("source video too large (500 MB max)", "red")
            return
        try:
            info = await asyncio.to_thread(video.probe, src)
        except Exception as e:
            self.sys_msg(f"not a readable video: {e}", "red")
            return
        if info["duration"] > video.MAX_DURATION_S:
            self.sys_msg("video too long (10 min max)", "red")
            return

        self.sys_msg(f"compressing {src.name} ({info['duration']:.0f}s)…")
        self.set_status("compressing video…")
        with tempfile.TemporaryDirectory(prefix="tor2vid-") as tmp:
            out = Path(tmp) / "out.mp4"
            try:
                await asyncio.to_thread(video.compress, src, out)
            except Exception as e:
                self.sys_msg(str(e), "red")
                self.set_status(f"connected to “{self.peer_nick}” ✓")
                return
            blob = out.read_bytes()
        if len(blob) > proto.MAX_VIDEO_BYTES:
            self.sys_msg("still over 60 MB after compression — send a shorter clip", "red")
            self.set_status(f"connected to “{self.peer_nick}” ✓")
            return
        if not self._require_connected():
            return

        chunks = [blob[i:i + proto.VIDEO_CHUNK]
                  for i in range(0, len(blob), proto.VIDEO_CHUNK)]
        await self.session.send({
            "t": "vmeta", "size": len(blob), "chunks": len(chunks),
            "sha256": hashlib.sha256(blob).hexdigest(),
        })
        self.sys_msg(f"sending video: {len(blob) // 1024 // 1024} MB compressed "
                     f"(tor is slow — this can take a while)")
        for i, chunk in enumerate(chunks, 1):
            if self.state != CONNECTED:
                self.sys_msg("video send aborted: session ended", "red")
                return
            await self.session.send(
                {"t": "vchunk", "data": base64.b64encode(chunk).decode()})
            self.set_status(f"sending video… {i * 100 // len(chunks)}%")
        self.set_status(f"connected to “{self.peer_nick}” ✓")
        self.sys_msg("video sent ✓", "green")

    # ---------- input ----------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        event.input.value = ""
        if not line:
            return
        if line.startswith("/"):
            await self.handle_command(line)
        else:
            await self.send_text(line)

    async def handle_command(self, line: str) -> None:
        cmd, _, arg = line.partition(" ")
        arg = arg.strip()
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
            case "/vid" | "/video":
                self.run_worker(self.send_video(arg), exclusive=False)
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
                await self.drop_session()
                self.sys_msg("disconnected")
            case "/help":
                for line_ in (
                    "/code                       — get a 5-digit pairing code to share",
                    "/join <code>                — connect using a peer's pairing code",
                    "/connect <contact-or-onion> — connect to a peer",
                    "/accept · /reject           — answer an incoming chat request",
                    "/img <path>                 — send an image (≤5 MB)",
                    "/vid <path>                 — send a video (auto-compressed, ≤10 min)",
                    "/add <name> [onion]         — save a contact (defaults to current peer)",
                    "/contacts · /delcontact <name>",
                    "/nick <name>                — set your display name (persists)",
                    "/disconnect · ctrl+q",
                ):
                    self.sys_msg(line_)
            case _:
                self.sys_msg(f"unknown command: {cmd} (try /help)", "red")

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

    async def action_quit(self) -> None:
        await self.stop_code()
        await self.drop_session()
        if self._server:
            self._server.close()
        await asyncio.to_thread(self.tor.shutdown)
        self.exit()


def main() -> None:
    Tor2App().run()
