"""Several live conversations at once: routing, badges, switching."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2 import proto
from tor2.app import Tor2App

async def _no_network(self):
    self.set_status("not connected")
Tor2App.start_network = _no_network

from tor2.clientserver import SERVER
from tor2.conv import ConvItem, Conversation, current as current_conv

class Fake:
    def __init__(self): self.sent=[]
    async def send(self, m): self.sent.append(m)
    async def close(self): pass

def text_of(conv):
    return "\n".join(getattr(l, "plain", str(l)) for l in conv.lines)

async def main():
    app = Tor2App(); app.nick = "kairo"
    async with app.run_test() as pilot:
        await pilot.pause()

        # two DMs and one server, all live at the same time
        dm1 = app.add_conv("dm:mia", "dm", "mia", Fake())
        dm1.state = "connected"; dm1.peer_nick = "mia"
        dm2 = app.add_conv("dm:bob", "dm", "bob", Fake())
        dm2.state = "connected"; dm2.peer_nick = "bob"
        srv = app.add_conv("srv:x", "server", "kairos", Fake())
        srv.state = SERVER
        srv.srv = {"onion":"x","name":"kairos","local":"kairos","channel":"general",
                   "channels":["general","random"],"admin":False,"online":[],
                   "buffers":{},"download":None}
        await pilot.pause(0.4)
        assert len(app.convs) == 3, app.convs
        assert app.active is srv
        print("three conversations open, active =", app.active.title)

        # --- a message arriving in a *background* DM must land in that DM only
        tok = current_conv.set(dm1)
        try:
            await app.handle_message({"t":"txt","body":"hi from mia"})
        finally:
            current_conv.reset(tok)
        await pilot.pause(0.3)
        assert "hi from mia" in text_of(dm1), text_of(dm1)
        assert "hi from mia" not in text_of(srv), "leaked into the wrong conversation"
        assert "hi from mia" not in text_of(dm2)
        assert dm1.unread == 1, dm1.unread
        print("background DM message routed correctly, unread =", dm1.unread)

        # --- and a message in the *active* conversation is not counted unread
        tok = current_conv.set(srv)
        try:
            app.srv_event({"t":"event","chan":"general","nick":"zoe","ts":0,
                           "body":"server hello","media":None,"id":5})
        finally:
            current_conv.reset(tok)
        await pilot.pause(0.3)
        assert srv.unread == 0, srv.unread
        assert "server hello" in "\n".join(
            str(getattr(l, "plain", l)) for l in srv.srv["buffers"]["general"])
        print("active conversation stays unread-free")

        # --- a mention in a background DM raises the @ flag
        tok = current_conv.set(dm2)
        try:
            await app.handle_message({"t":"txt","body":"@kairo you around?"})
        finally:
            current_conv.reset(tok)
        await pilot.pause(0.3)
        assert dm2.mentioned and dm2.unread == 1
        print("background mention flagged")

        # --- sidebar lists all three, with badges
        rows = {w.conv_key: w for w in app.query(ConvItem)}
        assert set(rows) == {"dm:mia","dm:bob","srv:x"}, list(rows)
        print("sidebar rows:", [c.sidebar_row(False).plain for c in app.convs.values()])

        # --- switching by click shows that conversation's log and clears its badge
        app.select_conv(dm1)
        await pilot.pause(0.3)
        assert app.active is dm1 and dm1.unread == 0
        assert app.session is dm1.session, "self.session must follow the active chat"
        assert app.state == "connected" and app.peer_nick == "mia"
        print("switched to mia; session/state/peer follow the active conversation")

        # ctrl+n cycles
        before = app.active.key
        await pilot.press("ctrl+n"); await pilot.pause(0.3)
        assert app.active.key != before
        print("ctrl+n cycles to", app.active.title)

        # --- sending goes to the *active* conversation's socket
        app.select_conv(dm2)
        await pilot.pause(0.2)
        await app.send_text("reply to bob")
        assert dm2.session.sent[-1] == {"t":"txt","body":"reply to bob"}
        assert not dm1.session.sent, "message went to the wrong peer!"
        print("outgoing message went to the active peer only")

        # --- closing one leaves the others running
        app.select_conv(dm1)
        await app.drop_session()
        await pilot.pause(0.3)
        assert "dm:mia" not in app.convs and len(app.convs) == 2
        assert app.active is not None and app.active.key in app.convs
        print("closed one; remaining:", [c.title for c in app.convs.values()])

        await app.action_quit()
    print("MULTI-SESSION TESTS PASSED")

def test_main():
    asyncio.run(main())