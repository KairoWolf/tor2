"""tor2 terminal UI."""

import asyncio
import base64
import getpass
import re
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Input, RichLog, Static

from . import proto
from .imgview import render_preview, validate_image
from .tornet import TorNet, normalize_onion

RECEIVED_DIR = Path.cwd() / "received"


class Tor2App(App):
    TITLE = "tor2"

    CSS = """
    #status {
        dock: top;
        height: 3;
        padding: 0 1;
        background: $panel;
        border-bottom: solid $primary;
    }
    #chat {
        padding: 0 1;
    }
    #inputbar {
        dock: bottom;
    }
    """

    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def __init__(self):
        super().__init__()
        self.nick = getpass.getuser()
        self.tor = TorNet(Path.cwd() / ".tordata")
        self.session: proto.Session | None = None
        self.peer_nick = "peer"
        self.local_port: int | None = None
        self._server: asyncio.Server | None = None

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        yield Static("starting…", id="status")
        with Vertical():
            yield RichLog(id="chat", wrap=True, markup=False)
        yield Input(placeholder="type a message, /connect <onion>, /img <path>, /help",
                    id="inputbar")
        yield Footer()

    # ---------- helpers ----------

    @property
    def chat(self) -> RichLog:
        return self.query_one("#chat", RichLog)

    def set_status(self, line: str) -> None:
        self.status_line = line
        self.query_one("#status", Static).update(line)

    def sys_msg(self, msg: str, style: str = "yellow") -> None:
        self.chat.write(Text(f"• {msg}", style=style))

    def chat_msg(self, who: str, body: str, mine: bool) -> None:
        t = Text()
        t.append(f"{who}: ", style="bold cyan" if mine else "bold magenta")
        t.append(body)
        self.chat.write(t)

    # ---------- startup ----------

    def on_mount(self) -> None:
        self.query_one("#inputbar", Input).focus()
        self.run_worker(self.start_network(), exclusive=False)

    async def start_network(self) -> None:
        self.sys_msg("bootstrapping tor (first run can take ~30s)…")

        def progress(pct: int) -> None:
            self.call_from_thread(self.set_status, f"tor bootstrap: {pct}%")

        try:
            await asyncio.to_thread(self.tor.launch, progress)
        except Exception as e:
            self.sys_msg(f"failed to start tor: {e}", "red")
            return

        self._server = await asyncio.start_server(
            self.on_incoming, "127.0.0.1", 0)
        self.local_port = self._server.sockets[0].getsockname()[1]

        self.set_status("publishing onion service…")
        try:
            onion = await asyncio.to_thread(self.tor.create_onion, self.local_port)
        except Exception as e:
            self.sys_msg(f"failed to publish onion service: {e}", "red")
            return

        self.set_status(f"your address: {onion}   |   not connected")
        self.sys_msg(f"your address: {onion}", "green")
        self.sys_msg("share it with your peer, or paste theirs with /connect <address>")

    # ---------- connection handling ----------

    async def on_incoming(self, reader, writer) -> None:
        if self.session is not None:
            writer.close()
            return
        try:
            session = await proto.handshake(reader, writer)
        except Exception as e:
            self.sys_msg(f"incoming connection failed handshake: {e}", "red")
            writer.close()
            return
        await self.attach_session(session, direction="incoming")

    async def do_connect(self, addr: str) -> None:
        if self.session is not None:
            self.sys_msg("already connected — /disconnect first", "red")
            return
        try:
            onion = normalize_onion(addr)
        except ValueError as e:
            self.sys_msg(str(e), "red")
            return
        self.sys_msg(f"connecting to {onion} (can take ~10s)…")
        try:
            reader, writer = await self.tor.dial(onion)
            session = await proto.handshake(reader, writer)
        except Exception as e:
            self.sys_msg(f"connection failed: {e}", "red")
            return
        await self.attach_session(session, direction="outgoing")

    async def attach_session(self, session: proto.Session, direction: str) -> None:
        if self.session is not None:  # lost a race with the other direction
            await session.close()
            return
        self.session = session
        self.sys_msg(f"secure session established ({direction})", "green")
        self.sys_msg(f"session fingerprint: {session.fingerprint}", "green")
        self.sys_msg("read the fingerprint to your peer — it must match on both ends")
        self.set_status(f"your address: {self.tor.onion_addr}   |   connected ✓")
        await session.send({"t": "hello", "nick": self.nick[:32]})
        self.run_worker(self.recv_loop(session), exclusive=False)

    async def recv_loop(self, session: proto.Session) -> None:
        try:
            while True:
                msg = await session.recv()
                self.handle_message(msg)
        except (asyncio.IncompleteReadError, ConnectionError):
            self.sys_msg("peer disconnected", "red")
        except Exception as e:
            self.sys_msg(f"session error: {e}", "red")
        finally:
            await self.drop_session()

    def handle_message(self, msg: dict) -> None:
        kind = msg.get("t")
        if kind == "hello":
            nick = str(msg.get("nick", "peer"))[:32] or "peer"
            self.peer_nick = re.sub(r"[^\w\- ]", "", nick) or "peer"
            self.sys_msg(f"peer identifies as “{self.peer_nick}”")
        elif kind == "txt":
            body = str(msg.get("body", ""))[:10000]
            self.chat_msg(self.peer_nick, body, mine=False)
        elif kind == "img":
            self.handle_image(msg)

    def handle_image(self, msg: dict) -> None:
        try:
            data = base64.b64decode(msg.get("data", ""), validate=True)
            if len(data) > proto.MAX_IMAGE_BYTES:
                raise ValueError("image exceeds size limit")
            fmt = validate_image(data)
        except Exception as e:
            self.sys_msg(f"rejected incoming image: {e}", "red")
            return
        # Never trust the sender's filename: build our own.
        RECEIVED_DIR.mkdir(exist_ok=True)
        n = 1
        while (dest := RECEIVED_DIR / f"img_{n:03d}.{fmt}").exists():
            n += 1
        dest.write_bytes(data)
        self.sys_msg(f"{self.peer_nick} sent an image → {dest}", "magenta")
        try:
            self.chat.write(render_preview(data))
        except Exception:
            self.sys_msg("(no inline preview available)", "yellow")

    async def drop_session(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
        self.peer_nick = "peer"
        if self.tor.onion_addr:
            self.set_status(f"your address: {self.tor.onion_addr}   |   not connected")

    # ---------- sending ----------

    async def send_text(self, body: str) -> None:
        if self.session is None:
            self.sys_msg("not connected — use /connect <onion address>", "red")
            return
        await self.session.send({"t": "txt", "body": body})
        self.chat_msg(self.nick, body, mine=True)

    async def send_image(self, path_str: str) -> None:
        if self.session is None:
            self.sys_msg("not connected — use /connect <onion address>", "red")
            return
        path = Path(path_str).expanduser()
        if not path.is_file():
            self.sys_msg(f"no such file: {path}", "red")
            return
        data = path.read_bytes()
        if len(data) > proto.MAX_IMAGE_BYTES:
            self.sys_msg("image too large (5 MB max)", "red")
            return
        try:
            validate_image(data)
        except Exception as e:
            self.sys_msg(f"not a supported image: {e}", "red")
            return
        self.sys_msg(f"sending {path.name} ({len(data) // 1024} KB)…")
        await self.session.send(
            {"t": "img", "data": base64.b64encode(data).decode()})
        self.chat.write(render_preview(data))
        self.sys_msg("image sent", "green")

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
            case "/img" | "/image":
                self.run_worker(self.send_image(arg), exclusive=False)
            case "/disconnect":
                await self.drop_session()
                self.sys_msg("disconnected")
            case "/nick":
                self.nick = re.sub(r"[^\w\- ]", "", arg)[:32] or self.nick
                self.sys_msg(f"you are now “{self.nick}” (applies to new sessions)")
            case "/help":
                self.sys_msg("/connect <onion>  — connect to a peer")
                self.sys_msg("/img <path>       — send an image (≤5 MB)")
                self.sys_msg("/nick <name>      — set your display name")
                self.sys_msg("/disconnect       — drop the current session")
                self.sys_msg("ctrl+q            — quit")
            case _:
                self.sys_msg(f"unknown command: {cmd} (try /help)", "red")

    async def action_quit(self) -> None:
        await self.drop_session()
        if self._server:
            self._server.close()
        await asyncio.to_thread(self.tor.shutdown)
        self.exit()


def main() -> None:
    Tor2App().run()
