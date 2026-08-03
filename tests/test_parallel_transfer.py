"""Sharded transfer: out-of-order offsets must reassemble byte-exactly."""
import asyncio, hashlib, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2 import media, proto
from tor2.serverd import Tor2Server

SIZE = 40 * 1024 * 1024

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp/"blob.bin"
        with src.open("wb") as f:
            block = os.urandom(1024*1024)
            for _ in range(SIZE // len(block)): f.write(block)
        sha = media.sha256_file(src)

        srv = Tor2Server(tmp/"srv")
        inv = srv.db.create_invite(uses=1, is_admin=True)
        aio = await asyncio.start_server(srv.handle_conn, "127.0.0.1", 0)
        port = aio.sockets[0].getsockname()[1]

        async def member():
            r,w = await asyncio.open_connection("127.0.0.1", port)
            s = await proto.handshake(r,w)
            await s.recv()
            return s

        # main connection authenticates with the invite; the rest reuse the token
        main_s = await member()
        await main_s.send({"t":"auth","nick":"kairo","invite":inv})
        ok = await main_s.recv(); token = ok["token"]; await main_s.recv()

        extra = []
        for i in range(3):
            s2 = await member()
            await s2.send({"t":"auth","nick":"kairo","token":token})
            o = await s2.recv(); assert o["t"]=="authok", o
            await s2.recv()
            extra.append(s2)
        print(f"opened {1+len(extra)} authenticated connections for one member")

        chunk = proto.chunk_size_for(SIZE)
        n = 1 + len(extra)
        span = SIZE // n
        bounds = [(i*span, SIZE if i==n-1 else (i+1)*span) for i in range(n)]
        meta = {"t":"mput","kind":"vid","ext":"mp4","chan":"general"}
        await main_s.send({**meta, "size":SIZE, "chunks":max(1,(SIZE+chunk-1)//chunk),
                           "sha256":sha})

        # deliberately start the LAST shard first, so offsets arrive out of order
        sessions = [main_s] + extra
        order = list(reversed(list(zip(sessions, bounds))))
        await asyncio.gather(*[
            media.send_file(sess, src, meta, "mchunk", chunk, sha,
                            announce=False, start=a, end=b)
            for sess, (a,b) in order])
        print("all shards sent (last shard first)")

        for _ in range(600):
            rows = srv.db.db.execute("SELECT id,size,sha256 FROM media").fetchall()
            if rows: break
            await asyncio.sleep(0.5)
        assert rows, "server never assembled the sharded upload"
        assert rows[0]["size"] == SIZE, rows[0]["size"]
        assert rows[0]["sha256"] == sha, "checksum mismatch after reassembly"
        # storage is encrypted at rest, so compare the decrypted bytes
        plain = srv.db.media_bytes(rows[0]["id"])
        assert hashlib.sha256(plain).hexdigest() == sha, \
            "stored bytes differ from source"
        print(f"reassembled {SIZE//1024//1024} MB from {n} shards, sha verified")

        # --- ranged fetch returns exactly the requested slice
        mid = rows[0]["id"]
        await main_s.send({"t":"fetch","id":mid,"start":1000,"end":1000+5*1024*1024})
        got = bytearray()
        while len(got) < 5*1024*1024:
            m = await asyncio.wait_for(main_s.recv(), timeout=60)
            if m["t"] == "mgchunk" and m.get("bin"):
                assert m["off"] == 1000 + len(got), (m["off"], len(got))
                got += m["bin"]
        expect = src.read_bytes()[1000:1000+5*1024*1024]
        assert bytes(got) == expect, "ranged fetch returned wrong bytes"
        print("ranged fetch returned the exact slice requested")

        aio.close(); srv.db.close()
    print("PARALLEL TRANSFER TESTS PASSED")

def test_main():
    asyncio.run(main())