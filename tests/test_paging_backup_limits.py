"""History paging, message deletion, flood limits, and backup/restore."""
import asyncio, subprocess, sys, tarfile, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2 import proto
from tor2.serverd import Tor2Server, RATE_MAX, make_backup
from tor2.serverdb import ServerDB

class C:
    def __init__(self, s): self.s=s; self.inbox=[]
    async def send(self, **kw): await self.s.send(kw)
    async def pump(self, t=0.3):
        try:
            while True: self.inbox.append(await asyncio.wait_for(self.s.recv(), timeout=t))
        except (asyncio.TimeoutError, TimeoutError): pass
        except Exception: pass
    async def expect(self, kind, timeout=8, where=None):
        end=asyncio.get_event_loop().time()+timeout
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
        tmp = Path(tmp)
        srv = Tor2Server(tmp/"srv")
        inv_a = srv.db.create_invite(uses=1, is_admin=True)
        inv_b = srv.db.create_invite(uses=1)
        aio = await asyncio.start_server(srv.handle_conn, "127.0.0.1", 0)
        port = aio.sockets[0].getsockname()[1]

        a = await conn(port); await a.expect("srvhello")
        await a.send(t="auth", nick="kairo", invite=inv_a); await a.expect("authok")
        await a.expect("histbatch")

        # --- seed 120 messages directly (bypassing the rate limit)
        for i in range(120):
            srv.db.add_message("general", 1, "kairo", f"msg{i}")

        # newest page
        await a.send(t="history", chan="general")
        h1 = await a.expect("histbatch")
        assert len(h1["msgs"]) == 50, len(h1["msgs"])
        assert h1["msgs"][-1]["body"] == "msg119", h1["msgs"][-1]
        oldest = h1["msgs"][0]["id"]
        print(f"first page: {len(h1['msgs'])} msgs ending at {h1['msgs'][-1]['body']}")

        # page back
        await a.send(t="history", chan="general", before=oldest)
        h2 = await a.expect("histbatch", where=lambda m: m.get("append"))
        assert h2["append"] is True and len(h2["msgs"]) == 50
        assert h2["msgs"][-1]["id"] < oldest
        print(f"older page: {len(h2['msgs'])} msgs, ending before id {oldest}")

        # page back again reaches the start
        await a.send(t="history", chan="general", before=h2["msgs"][0]["id"])
        h3 = await a.expect("histbatch", where=lambda m: m.get("append"))
        assert h3["msgs"][0]["body"] == "msg0", h3["msgs"][0]
        print("paged all the way back to msg0")

        # --- deletion
        b = await conn(port); await b.expect("srvhello")
        await b.send(t="auth", nick="mia", invite=inv_b); await b.expect("authok")
        await b.expect("histbatch")
        await b.send(t="post", chan="general", body="mia's message")
        ev = await b.expect("event", where=lambda m: m.get("body")=="mia's message")
        mid = ev["id"]

        # someone else cannot delete it
        await a.send(t="del", id=mid)   # a is admin, so this SHOULD work
        gone = await b.expect("deleted")
        assert gone["id"] == mid
        assert srv.db.message(mid) is None
        print("admin deleted another member's message")

        # non-admin deleting someone else's is refused
        await a.send(t="post", chan="general", body="kairo's message")
        ev2 = await a.expect("event", where=lambda m: m.get("body")=="kairo's message")
        await b.send(t="del", id=ev2["id"])
        err = await b.expect("srverr", where=lambda m: "your own" in m["msg"])
        assert srv.db.message(ev2["id"]) is not None
        print("non-admin blocked from deleting others':", err["msg"])

        # own message deletion works
        await b.send(t="post", chan="general", body="mia again")
        ev3 = await b.expect("event", where=lambda m: m.get("body")=="mia again")
        await b.send(t="del", id=ev3["id"])
        await b.expect("deleted", where=lambda m: m["id"]==ev3["id"])
        print("member deleted their own message")

        # --- flood control
        blocked = False
        for i in range(RATE_MAX + 8):
            await b.send(t="post", chan="general", body=f"flood{i}")
        await asyncio.sleep(1.0); await b.pump(0.6)
        blocked = any(m["t"]=="srverr" and "slow down" in m["msg"] for m in b.inbox)
        assert blocked, "flood was not rate limited"
        posted = sum(1 for m in b.inbox if m["t"]=="event" and str(m.get("body","")).startswith("flood"))
        assert posted <= RATE_MAX + 2, posted
        print(f"flood control: {posted} of {RATE_MAX+8} accepted, then refused")

        aio.close()

        # --- backup while running, then restore into a fresh dir
        (srv.data_dir/"onion.key").write_text("FAKEKEY")
        dest = make_backup(srv.data_dir, tmp/"backup.tar.gz")
        assert dest.is_file() and dest.stat().st_size > 0
        names = tarfile.open(dest).getnames()
        assert "server.db" in names and "onion.key" in names, names
        assert not (srv.data_dir/"server.db.backup").exists(), "temp snapshot left behind"
        print("backup contains:", [n for n in names if "/" not in n])

        restore = tmp/"restored"; restore.mkdir()
        with tarfile.open(dest) as t: t.extractall(restore)
        db2 = ServerDB(restore)
        assert db2.channels() == srv.db.channels()
        assert db2.member_count() == srv.db.member_count()
        assert (restore/"onion.key").read_text() == "FAKEKEY"
        print(f"restored: {db2.member_count()} members, channels {db2.channels()}")
        db2.close(); srv.db.close()
    print("BATCH TESTS PASSED")

def test_main():
    asyncio.run(main())