"""ban / unban / promote / demote, and the update checker's guards."""
import asyncio, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2 import proto, updater
from tor2.serverd import Tor2Server

class C:
    def __init__(self, s): self.s=s; self.inbox=[]; self.closed=False
    async def send(self, **kw): await self.s.send(kw)
    async def pump(self, t=0.4):
        try:
            while True: self.inbox.append(await asyncio.wait_for(self.s.recv(), timeout=t))
        except (asyncio.TimeoutError, TimeoutError): pass
        except Exception: self.closed=True
    async def expect(self, kind, timeout=6, where=None):
        end = asyncio.get_event_loop().time()+timeout
        while True:
            for i,m in enumerate(self.inbox):
                if m["t"]==kind and (where is None or where(m)): return self.inbox.pop(i)
            if asyncio.get_event_loop().time()>end:
                raise AssertionError(f"no {kind}: {[m['t'] for m in self.inbox]}")
            await self.pump(0.1)

async def conn(port):
    r,w = await asyncio.open_connection("127.0.0.1", port)
    return C(await proto.handshake(r,w))

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        srv = Tor2Server(Path(tmp))
        admin_inv = srv.db.create_invite(uses=1, is_admin=True)
        u1 = srv.db.create_invite(uses=1); u2 = srv.db.create_invite(uses=1)
        aio = await asyncio.start_server(srv.handle_conn, "127.0.0.1", 0)
        port = aio.sockets[0].getsockname()[1]

        a = await conn(port); await a.expect("srvhello")
        await a.send(t="auth", nick="kairo", invite=admin_inv); await a.expect("authok")
        b = await conn(port); await b.expect("srvhello")
        await b.send(t="auth", nick="mia", invite=u1)
        okb = await b.expect("authok"); btok = okb["token"]
        print("admin + member joined")

        # --- promote
        await a.send(t="promote", nick="mia")
        note = await b.expect("srverr", where=lambda m: "admin" in m["msg"])
        assert "now an admin" in note["msg"], note
        assert srv.db.member_by_nick("mia")["is_admin"] == 1
        print("promote OK:", note["msg"])

        # promoted member can now use admin commands
        await b.send(t="mkchan", name="mias-room")
        await asyncio.sleep(0.4)
        assert "mias-room" in srv.db.channels(), srv.db.channels()
        print("promoted member can create channels")

        # --- demote
        await a.send(t="demote", nick="mia"); await asyncio.sleep(0.4)
        assert srv.db.member_by_nick("mia")["is_admin"] == 0
        await b.send(t="mkchan", name="nope"); await asyncio.sleep(0.4)
        assert "nope" not in srv.db.channels()
        print("demote OK, admin powers revoked")

        # cannot demote the last admin
        await a.send(t="demote", nick="kairo")
        err = await a.expect("srverr", where=lambda m: "last admin" in m["msg"])
        print("last-admin guard:", err["msg"])

        # --- ban
        await a.send(t="ban", nick="mia", reason="spam")
        await asyncio.sleep(0.6)
        assert srv.db.is_banned("mia")
        assert srv.db.member_by_nick("mia") is None, "ban did not revoke membership"
        print("ban OK, membership revoked")

        # banned token cannot reconnect
        c = await conn(port); await c.expect("srvhello")
        await c.send(t="auth", token=btok, nick="mia")
        err = await c.expect("srverr")
        assert "no longer valid" in err["msg"] or "banned" in err["msg"], err
        # nor with a fresh invite under the same name
        d = await conn(port); await d.expect("srvhello")
        await d.send(t="auth", nick="mia", invite=u2)
        err = await d.expect("srverr")
        assert "banned" in err["msg"], err
        print("banned name blocked from rejoining:", err["msg"])

        # --- bans list + unban
        await a.send(t="bans")
        lst = await a.expect("srverr", where=lambda m: "banned:" in m["msg"])
        assert "mia" in lst["msg"] and "spam" in lst["msg"]
        await a.send(t="unban", nick="mia")
        await a.expect("srverr", where=lambda m: "unbanned" in m["msg"])
        assert not srv.db.is_banned("mia")
        print("bans list + unban OK")

        # admin cannot ban themselves
        await a.send(t="ban", nick="kairo")
        err = await a.expect("srverr", where=lambda m: "yourself" in m["msg"])
        print("self-ban blocked")

        # --- autoupdate defaults off and reports state
        await a.send(t="autoupdate", mode="")
        st = await a.expect("srverr", where=lambda m: "auto-update is" in m["msg"])
        assert "OFF" in st["msg"], st
        print("autoupdate default:", st["msg"])

        aio.close(); srv.db.close()

    # --- updater guards (must refuse a dirty tree / wrong remote)
    try:
        info = updater.check()
        print(f"updater.check: behind={info['behind']} branch={info['branch']}")
    except updater.UpdateError as e:
        assert "local changes" in str(e) or "not a git" in str(e), e
        print("updater refuses to update a dirty checkout:", e)
    print("ADMIN TESTS PASSED")

def test_main():
    asyncio.run(main())