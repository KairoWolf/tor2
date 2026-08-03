"""Every command tor2 advertises must actually route somewhere.

/ban, /promote and /autoupdate were implemented end to end but never added
to the command table, so they answered "unknown command" — the protocol
tests passed because they spoke the wire format directly and never went
through the command parser.
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tor2.app import Tor2App
from tor2.clientserver import SERVER


class Fake:
    def __init__(self):
        self.sent = []

    async def send(self, m):
        self.sent.append(m)

    async def send_binary(self, header, blob):
        self.sent.append({**header, "bin": blob})

    async def close(self):
        pass


ARGS = {                       # commands that need an argument to get past validation
    "/nick": "someone", "/join": "12345", "/connect": "nobody",
    "/add": "friend", "/delcontact": "friend", "/ch": "general",
    "/channel": "general", "/mkchan": "newchan", "/rmchan": "random",
    "/kick": "mia", "/ban": "mia", "/unban": "mia", "/promote": "mia",
    "/demote": "mia", "/get": "1", "/del": "1", "/save": "1", "/play": "1",
    "/img": "/nonexistent.png", "/vid": "/nonexistent.mp4",
    "/big-vid": "/nonexistent.mp4", "/audio": "/nonexistent.mp3",
    "/joinserver": "abc def", "/server": "", "/autoupdate": "",
    "/newinvite": "1", "/update": "check",
}

# these deliberately reach the network or replace the screen
SKIP = {"/update", "/joinserver", "/server", "/connect", "/join", "/code",
        "/settings", "/quit"}


def collect(app):
    said = []
    app.sys_msg = lambda m, style="x": said.append(str(m))
    return said


def setup_conv(app, server_mode: bool):
    """Fresh connected state — some commands (/disconnect, /leave) end it."""
    for key in list(app.convs):
        app.convs.pop(key)
    if server_mode:
        app.add_conv("srv:t", "server", "s", Fake())
        app.state = SERVER
        app.srv = {"onion": "x", "name": "s", "local": "s",
                   "channel": "general",
                   "channels": ["general", "random"], "admin": True,
                   "online": ["kairo"], "buffers": {}, "download": None}
    else:
        conv = app.add_conv("dm:x", "dm", "x", Fake())
        conv.state = "connected"


async def run_all(server_mode: bool):
    app = Tor2App()
    app.nick = "kairo"
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        unknown = []
        for cmd in app.ALL_COMMANDS:
            if cmd in SKIP:
                continue
            if server_mode and not cmd.startswith("/") :
                continue
            setup_conv(app, server_mode)
            await pilot.pause(0.05)
            said = collect(app)
            try:
                await app.handle_command(f"{cmd} {ARGS.get(cmd, '')}".strip())
            except Exception as e:                 # a crash is also a failure
                unknown.append(f"{cmd} raised {type(e).__name__}: {e}")
                continue
            await pilot.pause(0.05)
            if any("unknown command" in m for m in said):
                unknown.append(cmd)
        app.session = None
        await app.action_quit()
    return unknown


def test_every_command_routes_in_server_mode():
    missing = asyncio.run(run_all(server_mode=True))
    assert not missing, f"commands not routed on a server: {missing}"


def test_every_command_routes_in_dm_mode():
    missing = asyncio.run(run_all(server_mode=False))
    # server-only commands are expected here, but they must explain themselves
    # rather than claiming the command does not exist
    assert not missing, f"commands not routed in a direct chat: {missing}"


def test_server_only_commands_explain_themselves_in_a_dm():
    async def go():
        app = Tor2App()
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            conv = app.add_conv("dm:x", "dm", "x", Fake())
            conv.state = "connected"
            said = collect(app)
            await app.handle_command("/ch general")
            await pilot.pause(0.1)
            app.session = None
            await app.action_quit()
            return said
    said = asyncio.run(go())
    assert any("only works on a server" in m for m in said), said
    assert not any("unknown command" in m for m in said), said


def test_readme_commands_are_all_known():
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    documented = set(re.findall(r"`(/[a-z\-]+)", readme))
    known = set(Tor2App.ALL_COMMANDS) | {"/image", "/video", "/bigvid", "/mp3",
                                         "/config", "/delete", "/channel"}
    assert documented <= known, f"documented but unknown: {documented - known}"
