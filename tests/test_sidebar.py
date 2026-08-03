"""Sidebar UX: arrow-key switching, clicking, active highlight, typing intact."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2.app import Tor2App

# UI-only tests: never launch a real Tor instance
async def _no_network(self):
    self.set_status("not connected")
Tor2App.start_network = _no_network

from tor2.clientserver import ChannelItem, SERVER

class FakeSession:
    def __init__(self): self.sent = []
    async def send(self, msg): self.sent.append(msg)
    async def close(self): pass

async def main():
    app = Tor2App()
    async with app.run_test() as pilot:
        await pilot.pause()
        # sidebar hidden in DM mode
        assert not app.query_one("#sidebar").display, "sidebar visible in DM mode"

        # fake an established server session
        conv = app.add_conv("srv:test", "server", "kairos", FakeSession())
        app.state = SERVER
        app.srv = {"onion": "x.onion", "name": "kairos-server", "local": "kairos",
                   "channel": "general", "channels": ["general", "random", "music"],
                   "admin": True, "online": ["kairo", "mia"], "buffers": {}, "download": None}
        app.refresh_view()
        await pilot.pause(0.3)

        items = list(app.query(ChannelItem))
        assert [i.channel for i in items] == ["general", "random", "music"], items
        active = [i.channel for i in items if i.has_class("-active")]
        assert active == ["general"], active
        print("sidebar renders channels, active =", active[0])

        # down arrow -> next channel
        await pilot.press("down"); await pilot.pause(0.3)
        assert app.srv["channel"] == "random", app.srv["channel"]
        assert app.session.sent[-1] == {"t": "switch", "chan": "random"}
        active = [i.channel for i in app.query(ChannelItem) if i.has_class("-active")]
        assert active == ["random"], active
        print("down arrow -> #random, highlight follows")

        # wraps around
        await pilot.press("down"); await pilot.pause(0.2)
        await pilot.press("down"); await pilot.pause(0.2)
        assert app.srv["channel"] == "general", app.srv["channel"]
        print("down wraps back to #general")

        # up arrow goes backwards
        await pilot.press("up"); await pilot.pause(0.3)
        assert app.srv["channel"] == "music", app.srv["channel"]
        print("up arrow -> #music")

        # clicking a channel selects it
        target = [i for i in app.query(ChannelItem) if i.channel == "random"][0]
        await pilot.click(target); await pilot.pause(0.4)
        assert app.srv["channel"] == "random", app.srv["channel"]
        active = [i.channel for i in app.query(ChannelItem) if i.has_class("-active")]
        assert active == ["random"], active
        print("click -> #random")

        # typing still works and does not switch channels
        before = app.srv["channel"]
        await pilot.press(*"hello there"); await pilot.pause(0.2)
        assert app.query_one("#inputbar").value == "hello there"
        assert app.srv["channel"] == before
        await pilot.press("enter"); await pilot.pause(0.3)
        assert app.session.sent[-1]["t"] == "post"
        assert app.session.sent[-1]["body"] == "hello there"
        print("typing + enter posts to the active channel")

        # new channel from server appears in sidebar
        await app.handle_server_msg({"t": "members", "channels": ["general", "random", "music", "art"],
                                     "online": ["kairo"]})
        await pilot.pause(0.5)
        assert [i.channel for i in app.query(ChannelItem)][-1] == "art"
        print("new channel appears in sidebar")

        # arrows do nothing in DM mode
        await app.drop_session()
        await pilot.pause(0.3)
        assert not app.query_one("#sidebar").display
        await pilot.press("down"); await pilot.pause(0.2)
        print("DM mode: sidebar hidden, arrows harmless")

        await app.action_quit()
    print("SIDEBAR TESTS PASSED")

def test_main():
    asyncio.run(main())