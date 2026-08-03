"""Fast tests: double-encryption protocol over a local socket + video pipeline."""
import asyncio, io, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2 import proto, video

async def proto_tests():
    fut = asyncio.get_event_loop().create_future()
    async def on_conn(r, w):
        fut.set_result(await proto.handshake(r, w))
    server = await asyncio.start_server(on_conn, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    r, w = await asyncio.open_connection("127.0.0.1", port)
    client = await proto.handshake(r, w)
    srv = await fut
    assert client.fingerprint == srv.fingerprint
    await client.send({"t": "txt", "body": "hi"})
    assert (await srv.recv())["body"] == "hi"
    print("double-layer round trip OK")

    # outer-layer-only forgery must fail: encrypt with outer box but skip inner
    forged = srv.box.encrypt(b'{"t":"txt","body":"forged"}')
    srv.writer.write(len(forged).to_bytes(4, "big") + forged)
    await srv.writer.drain()
    try:
        await client.recv()
        raise AssertionError("outer-only frame was accepted!")
    except Exception as e:
        print(f"outer-only forgery rejected OK ({type(e).__name__})")
    server.close()

def video_tests():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.mp4"
        # generate a 3s test clip
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc=duration=3:size=1280x720:rate=24",
                        "-pix_fmt", "yuv420p", str(src)], check=True)
        info = video.probe(src)
        assert 2.5 < info["duration"] < 3.5, info
        out = Path(tmp) / "out.mp4"
        video.compress(src, out)
        assert out.stat().st_size > 0
        video.validate_received(out)
        print(f"video compress OK ({src.stat().st_size//1024} KB → {out.stat().st_size//1024} KB)")
        # non-video must be rejected
        junk = Path(tmp) / "junk.mp4"
        junk.write_bytes(b"not a video" * 100)
        try:
            video.validate_received(junk)
            raise AssertionError("junk accepted as video!")
        except ValueError:
            print("junk video rejected OK")

asyncio.run(proto_tests())
video_tests()
def test_main():
    print("ALL FAST TESTS PASSED")

