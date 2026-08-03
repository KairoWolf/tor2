"""Output must be visible before any conversation exists (and after the last closes)."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2.app import Tor2App
async def _no_network(self):
    self.set_status("not connected")
Tor2App.start_network = _no_network

class Fake:
    def __init__(self): self.sent=[]
    async def send(self, m): self.sent.append(m)
    async def close(self): pass

def screen(app):
    out = []
    for strip in app.raw_log.lines:
        segs = getattr(strip, "_segments", None) or getattr(strip, "segments", [])
        out.append("".join(getattr(s, "text", "") for s in segs))
    return "\n".join(out)

async def main():
    app = Tor2App()
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        assert app.active is None, "no conversation should exist at startup"

        # the welcome line written on mount must be on screen
        assert "welcome" in screen(app).lower(), f"startup text invisible: {screen(app)!r}"
        print("startup text visible")

        # /help with nothing connected
        await app.handle_command("/help")
        await pilot.pause(0.3)
        out = screen(app)
        for expected in ("/connect", "/joinserver", "/code", "/img", "/update"):
            assert expected in out, f"{expected} missing from help:\n{out}"
        print("/help visible with no conversation:",
              len([l for l in out.split(chr(10)) if l.strip()]), "lines")

        # an error message is visible too
        await app.handle_command("/bogus")
        await pilot.pause(0.3)
        assert "unknown command" in screen(app), "errors invisible"
        print("errors visible")

        # after opening and closing a conversation, output is visible again
        conv = app.add_conv("dm:x", "dm", "x", Fake())
        conv.state = "connected"
        await pilot.pause(0.3)
        await app.drop_session()
        await pilot.pause(0.3)
        assert app.active is None and not app.convs
        app.sys_msg("back to no conversation")
        await pilot.pause(0.2)
        assert "back to no conversation" in screen(app), screen(app)[-200:]
        print("visible again after the last conversation closes")

        await app.action_quit()
    print("VISIBILITY TESTS PASSED")

def test_main():
    asyncio.run(main())