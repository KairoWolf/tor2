"""Server daemon logic over loopback sockets (no Tor): auth, channels,
broadcast, history, media upload/fetch, admin ops, permissions."""
import asyncio, base64, hashlib, io, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image
from tor2 import proto
from tor2.serverd import Tor2Server

class FakeClient:
    def __init__(self, session): self.s = session; self.inbox = []; self.closed = False
    async def send(self, **kw): await self.s.send(kw)
    async def expect(self, kind, timeout=5, where=None):
        end = asyncio.get_event_loop().time() + timeout
        while True:
            for i, m in enumerate(self.inbox):
                if m["t"] == kind and (where is None or where(m)):
                    return self.inbox.pop(i)
            if asyncio.get_event_loop().time() > end:
                raise AssertionError(f"no {kind}; got {[m['t'] for m in self.inbox]}")
            await self.pump(0.05)
    async def pump(self, t=0.2):
        try:
            while True:
                self.inbox.append(await asyncio.wait_for(self.s.recv(), timeout=t))
        except (asyncio.TimeoutError, TimeoutError): pass
        except (asyncio.IncompleteReadError, ConnectionError): self.closed = True

async def connect(port):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    return FakeClient(await proto.handshake(r, w))

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        srv = Tor2Server(Path(tmp))
        admin_code = srv.db.create_invite(uses=1, is_admin=True)
        user_code = srv.db.create_invite(uses=1)
        aio = await asyncio.start_server(srv.handle_conn, "127.0.0.1", 0)
        port = aio.sockets[0].getsockname()[1]

        # --- auth: bad invite rejected
        bad = await connect(port)
        await bad.expect("srvhello")
        await bad.send(t="auth", nick="mallory", invite="nope-nope-nope")
        err = await bad.expect("srverr")
        assert "invalid" in err["msg"], err
        print("bad invite rejected OK")

        # --- unauthenticated cannot post
        sneaky = await connect(port)
        await sneaky.expect("srvhello")
        await sneaky.send(t="post", chan="general", body="hax")
        await sneaky.pump(0.5)
        assert srv.db.history("general") == [], "unauthed post stored!"
        print("unauthed post blocked OK")

        # --- admin joins
        a = await connect(port); await a.expect("srvhello")
        await a.send(t="auth", nick="kairo", invite=admin_code)
        ok = await a.expect("authok")
        assert ok["admin"] is True and ok["nick"] == "kairo"
        token = ok["token"]; assert token
        await a.expect("histbatch")
        print("admin auth OK")

        # --- member joins
        b = await connect(port); await b.expect("srvhello")
        await b.send(t="auth", nick="mia", invite=user_code)
        okb = await b.expect("authok")
        assert okb["admin"] is False
        btoken = okb["token"]
        print("member auth OK")

        # --- chat broadcast
        await a.send(t="post", chan="general", body="hello everyone")
        ev = await b.expect("event")
        assert ev["body"] == "hello everyone" and ev["nick"] == "kairo"
        print("broadcast OK")

        # --- non-admin blocked from admin ops
        await b.send(t="mkchan", name="hax")
        err = await b.expect("srverr")
        assert "admin only" in err["msg"], err
        await asyncio.sleep(0.3)
        assert "hax" not in srv.db.channels()
        print("admin permission enforced OK")

        # --- admin creates channel, member switches and posts
        await a.send(t="mkchan", name="random")
        for _ in range(40):
            if "random" in srv.db.channels(): break
            await asyncio.sleep(0.1)
        assert "random" in srv.db.channels(), srv.db.channels()
        await b.send(t="switch", chan="random")
        hb = await b.expect("histbatch", where=lambda m: m["chan"] == "random")
        await b.send(t="post", chan="random", body="over here")
        ev = await a.expect("event", where=lambda m: m.get("body") == "over here")
        assert ev["chan"] == "random"
        print("channels OK:", srv.db.channels())

        # --- image upload is pushed inline
        buf = io.BytesIO(); Image.new("RGB", (24, 16), (10, 200, 90)).save(buf, "PNG")
        blob = buf.getvalue()
        await a.send(t="mput", kind="img", ext="png", chan="general", size=len(blob),
                     chunks=1, sha256=hashlib.sha256(blob).hexdigest())
        await a.send(t="mchunk", data=base64.b64encode(blob).decode())
        ev = await b.expect("event", where=lambda m: (m.get("media") or {}).get("kind") == "img")
        assert ev["media"]["kind"] == "img" and base64.b64decode(ev["inline"]) == blob
        print("image upload + inline push OK")

        # --- bad checksum rejected
        await a.send(t="mput", kind="img", ext="png", chan="general", size=len(blob),
                     chunks=1, sha256="0"*64)
        await a.send(t="mchunk", data=base64.b64encode(blob).decode())
        err = await a.expect("srverr"); assert "checksum" in err["msg"]
        print("checksum enforcement OK")

        # --- video upload announced (not pushed), then fetched
        vid = b"\x00\x00\x00\x20ftypmp42" + b"v" * (600*1024)
        chunks = [vid[i:i+proto.VIDEO_CHUNK] for i in range(0, len(vid), proto.VIDEO_CHUNK)]
        await a.send(t="mput", kind="vid", ext="mp4", chan="general", size=len(vid),
                     chunks=len(chunks), sha256=hashlib.sha256(vid).hexdigest())
        for i, ch in enumerate(chunks):
            await a.s.send_binary({"t": "mchunk", "off": i * proto.VIDEO_CHUNK}, ch)
        ev = await b.expect("event", timeout=15, where=lambda m: (m.get("media") or {}).get("kind") == "vid")
        assert ev["media"]["kind"] == "vid" and "inline" not in ev
        mid = ev["media"]["id"]
        await b.send(t="switch", chan="general")
        await b.expect("histbatch", where=lambda m: m["chan"] == "general")
        await b.send(t="fetch", id=mid)
        hdr = await b.expect("mget", timeout=10)
        got = b""
        for _ in range(hdr["chunks"]):
            m = await b.expect("mgchunk", timeout=10)
            got += m.get("bin") if m.get("bin") is not None else base64.b64decode(m["data"])
        assert got == vid, "fetched video mismatch"
        print("video announce + on-demand fetch OK")

        # --- token reconnect gives history
        b.s.writer.close()
        c = await connect(port); await c.expect("srvhello")
        await c.send(t="auth", token=btoken, nick="ignored")
        okc = await c.expect("authok")
        # empty, never null: a null is stringified as "null" by some clients
        assert okc["nick"] == "mia" and not okc["token"]
        assert okc["token"] == "", f"token must be empty, not {okc['token']!r}"
        hist = await c.expect("histbatch")
        assert any(m.get("body") == "hello everyone" for m in hist["msgs"]), hist
        print("token reconnect + history OK")

        # --- kick revokes membership
        await a.send(t="kick", nick="mia")
        await asyncio.sleep(0.5); await c.pump(0.5)
        assert srv.db.member_by_token(btoken) is None
        d = await connect(port); await d.expect("srvhello")
        await d.send(t="auth", token=btoken, nick="mia")
        err = await d.expect("srverr"); assert "no longer valid" in err["msg"]
        print("kick + token revocation OK")

        aio.close(); srv.db.close()
    print("SERVER LOCAL TESTS PASSED")

def test_main():
    asyncio.run(main())