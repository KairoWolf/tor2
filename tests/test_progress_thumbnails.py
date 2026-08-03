"""Compression progress reporting, thumbnails, and end-to-end thumbnail delivery."""
import asyncio, base64, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2 import media, proto, video
from tor2.imgview import render_preview, validate_image
from tor2.serverd import Tor2Server

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "clip.mp4"
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",
                        "testsrc=duration=6:size=640x480:rate=24",
                        "-pix_fmt","yuv420p",str(src)], check=True)

        # --- compression progress
        info = video.probe(src)
        seen = []
        out = tmp / "out.mp4"
        video.compress(src, out, on_progress=seen.append, duration=info["duration"])
        assert seen, "no progress callbacks at all"
        assert seen == sorted(seen), f"progress went backwards: {seen[:6]}"
        assert 0.0 <= seen[0] and seen[-1] == 1.0, seen[:3]
        print(f"compression progress: {len(seen)} updates, "
              f"{seen[0]:.2f} → {seen[-1]:.2f}")

        # --- thumbnail
        th = video.thumbnail(out)
        assert th and len(th) <= video.MAX_THUMB_BYTES, th and len(th)
        assert validate_image(th) == "jpeg"
        render_preview(th, width=44)
        print(f"thumbnail: {len(th)//1024} KB jpeg, renders fine")

        # very short clip still yields a thumbnail (falls back to t=0)
        short = tmp / "short.mp4"
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",
                        "testsrc=duration=0.3:size=160x120:rate=10",
                        "-pix_fmt","yuv420p",str(short)], check=True)
        assert video.thumbnail(short), "no thumbnail for a very short clip"
        print("short-clip thumbnail OK")

        # --- thumbnail survives upload → stored → announced to members
        srv = Tor2Server(tmp / "srv")
        inv = srv.db.create_invite(uses=1, is_admin=True)
        aio = await asyncio.start_server(srv.handle_conn, "127.0.0.1", 0)
        port = aio.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        s = await proto.handshake(r, w)
        await s.recv(); await s.send({"t":"auth","nick":"kairo","invite":inv})
        await s.recv(); await s.recv()

        sha = media.sha256_file(out)
        size = out.stat().st_size
        await media.send_file(s, out, {"t":"mput","kind":"vid","ext":"mp4",
                                       "chan":"general",
                                       "thumb": base64.b64encode(th).decode()},
                              "mchunk", proto.chunk_size_for(size), sha)
        ev = None
        for _ in range(60):
            m = await asyncio.wait_for(s.recv(), timeout=30)
            if m["t"] == "event" and m.get("media"):
                ev = m; break
        assert ev, "no event for the upload"
        assert ev["media"]["kind"] == "vid"
        assert ev["media"].get("thumb"), "thumbnail did not reach members"
        got = base64.b64decode(ev["media"]["thumb"])
        assert got == th, "thumbnail corrupted in transit"
        print("thumbnail delivered to members with the video announcement")

        # and it comes back in history too
        await s.send({"t": "history", "chan": "general"})
        hb = None
        for _ in range(20):
            m = await asyncio.wait_for(s.recv(), timeout=20)
            if m["t"] == "histbatch": hb = m; break
        assert hb and any((x.get("media") or {}).get("thumb") for x in hb["msgs"]), \
            "thumbnail missing from history"
        print("thumbnail present in history too")
        aio.close(); srv.db.close()
    print("PROGRESS + THUMBNAIL TESTS PASSED")

def test_main():
    asyncio.run(main())