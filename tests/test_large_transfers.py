"""Big-file transfer: correctness + flat memory + server disk guard."""
import asyncio, os, resource, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2 import media, proto
from tor2.serverd import Tor2Server

def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

SIZE = 700 * 1024 * 1024   # 700 MB — far beyond any in-memory design

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "big.bin"
        # deterministic pseudo-random content, written streaming
        with src.open("wb") as f:
            block = os.urandom(1024 * 1024)
            for _ in range(SIZE // len(block)):
                f.write(block)
        print(f"source: {src.stat().st_size / 1024 / 1024:.0f} MB")
        base_rss = rss_mb()
        sha = media.sha256_file(src)
        print(f"streamed sha256 ok, rss after hashing: {rss_mb():.0f} MB")

        # --- transfer it through a real (loopback) tor2 session ---
        srv = Tor2Server(tmp / "srvdata")
        invite = srv.db.create_invite(uses=1, is_admin=True)
        aio = await asyncio.start_server(srv.handle_conn, "127.0.0.1", 0)
        port = aio.sockets[0].getsockname()[1]

        r, w = await asyncio.open_connection("127.0.0.1", port)
        s = await proto.handshake(r, w)
        assert (await s.recv())["t"] == "srvhello"
        await s.send({"t": "auth", "nick": "kairo", "invite": invite})
        ok = await s.recv(); assert ok["t"] == "authok", ok
        await s.recv()  # histbatch

        chunk = proto.chunk_size_for(SIZE)
        print(f"chunk size: {chunk // 1024} KB")
        await media.send_file(s, src, {"t": "mput", "kind": "vid", "ext": "mp4",
                                       "chan": "general"}, "mchunk", chunk, sha)
        # wait for the server to store it
        for _ in range(300):
            rows = srv.db.db.execute("SELECT id,size,sha256 FROM media").fetchall()
            if rows: break
            await asyncio.sleep(0.5)
        assert rows, "server never stored the upload"
        row = rows[0]
        assert row["size"] == SIZE, (row["size"], SIZE)
        assert row["sha256"] == sha, "sha mismatch"
        stored = srv.db.media_path(row["id"])
        assert stored.is_file()
        # encrypted on disk, so the file is larger than the plaintext it holds
        assert stored.stat().st_size >= SIZE
        print(f"server stored {row['size'] / 1024 / 1024:.0f} MB, sha verified")
        peak = rss_mb()
        print(f"peak rss: {peak:.0f} MB (baseline {base_rss:.0f} MB)")
        assert peak < 400, f"memory blew up: {peak:.0f} MB — not streaming!"

        # --- disk guard ---
        import shutil as _sh
        real = _sh.disk_usage
        class FakeUsage:
            def __init__(s, total, used, free): s.total, s.used, s.free = total, used, free
        try:
            import tor2.media as m, tor2.serverd as sd
            _sh.disk_usage = lambda p: FakeUsage(100*10**9, 85*10**9, 15*10**9)
            try:
                srv.check_storage(1024)
                raise AssertionError("upload allowed at 85% disk!")
            except ValueError as e:
                print("guard at 85% full ->", e)
            # 70% used, but a 15 GB upload would cross 80%
            _sh.disk_usage = lambda p: FakeUsage(100*10**9, 70*10**9, 30*10**9)
            try:
                srv.check_storage(15*10**9)
                raise AssertionError("upload allowed that crosses the limit!")
            except ValueError as e:
                print("guard on projected usage ->", e)
            # 70% used, small upload is fine
            srv.check_storage(1024)
            print("small upload at 70% allowed")
        finally:
            _sh.disk_usage = real

        aio.close(); srv.db.close()
    print("BIG VIDEO TESTS PASSED")

def test_main():
    asyncio.run(main())