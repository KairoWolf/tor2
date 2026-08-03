"""Unread badges, @mentions, and auto-reconnect behaviour."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2 import proto, store
from tor2.app import Tor2App

# UI-only tests: never launch a real Tor instance
async def _no_network(self):
    self.set_status("not connected")
Tor2App.start_network = _no_network

from tor2.clientserver import ChannelItem, SERVER

class Fake:
    def __init__(self): self.sent=[]
    async def send(self, m): self.sent.append(m)
    async def close(self): pass

def ev(chan, nick, body, mid=1):
    return {"t":"event","chan":chan,"nick":nick,"ts":0,"body":body,"media":None,"id":mid}

async def main():
    app = Tor2App()
    app.nick = "kairo"
    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.add_conv("srv:test", "server", "kairos", Fake())
        app.state = SERVER
        app.srv = {"onion":"x.onion","name":"kairos","local":"k","channel":"general",
                   "channels":["general","random","music"],"admin":False,
                   "online":[],"buffers":{},"download":None}
        app.refresh_view()
        await pilot.pause(0.4)

        # --- mention detection
        assert app.mentions_me("hey @kairo look")
        assert app.mentions_me("kairo can you check")
        assert app.mentions_me("KAIRO?")            # case-insensitive
        assert not app.mentions_me("kairos-server is up")   # not a whole word
        assert not app.mentions_me("nothing to see")
        print("mention detection OK")

        # --- unread counts accumulate for other channels only
        app.srv_event(ev("random","mia","first"))
        app.srv_event(ev("random","mia","second"))
        app.srv_event(ev("music","mia","hello"))
        app.srv_event(ev("general","mia","in the open channel"))
        await pilot.pause(0.3)
        assert app.srv["unread"] == {"random":2,"music":1}, app.srv["unread"]
        print("unread counts:", app.srv["unread"])

        # own messages never count as unread
        app.srv_event(ev("random","kairo","my own"))
        assert app.srv["unread"]["random"] == 2
        print("own messages ignored")

        # --- badges rendered in the sidebar
        rows = {w.channel: w.row_text for w in app.query(ChannelItem)}
        assert "2" in rows["random"] and "1" in rows["music"], rows
        assert rows["general"].strip() == "# general", rows["general"]
        print("sidebar badges:", {k: v.strip() for k, v in rows.items()})

        # --- a mention marks the channel with @
        app.srv_event(ev("music","mia","ping @kairo please"))
        await pilot.pause(0.3)
        assert "music" in app.srv["mentions"]
        rows = {w.channel: w.row_text for w in app.query(ChannelItem)}
        assert "@" in rows["music"], rows["music"]
        print("mention badge:", rows["music"].strip())

        # --- switching clears that channel's unread + mention
        await app.server_switch("music")
        await pilot.pause(0.3)
        assert "music" not in app.srv["unread"] and "music" not in app.srv["mentions"]
        rows = {w.channel: w.row_text for w in app.query(ChannelItem)}
        assert "@" not in rows["music"] and rows["music"].strip() == "# music"
        print("switching clears the badge")

        # --- manual disconnect must not trigger auto-reconnect
        conv.manual_disconnect = False
        await app.handle_command("/disconnect")
        await pilot.pause(0.3)
        # the flag lives on the conversation, which server_loop consults before
        # deciding whether to reconnect
        assert conv.manual_disconnect is True, "manual disconnect not recorded"
        assert conv.key not in app.convs, "conversation should be closed"
        print("/disconnect records intent and closes the conversation")

        await app.action_quit()
    print("UNREAD + MENTION TESTS PASSED")

def test_main():
    asyncio.run(main())