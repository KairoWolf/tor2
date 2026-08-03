"""/big-vid command wiring in both DM and server mode."""
import asyncio, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2 import proto
from tor2.app import Tor2App

# UI-only tests: never launch a real Tor instance
async def _no_network(self):
    self.set_status("not connected")
Tor2App.start_network = _no_network

from tor2.clientserver import SERVER

class FakeSession:
    def __init__(self): self.sent = []
    async def send(self, m): self.sent.append(m)
    async def send_binary(self, header, blob):
        self.sent.append({**header, "bin": blob})
    async def close(self): pass

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        vid = Path(tmp) / "long.mp4"
        # 12s clip — longer than the 10-min /vid limit? no, but exercises the path
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",
                        "testsrc=duration=12:size=320x240:rate=15",
                        "-pix_fmt","yuv420p",str(vid)], check=True)
        app = Tor2App()
        async with app.run_test() as pilot:
            await pilot.pause()
            msgs = []
            app.sys_msg = lambda m, s="x": msgs.append(m)

            # --- DM mode
            app.session = FakeSession(); app.state = "connected"; app.peer_nick = "mia"
            await app.handle_command(f"/big-vid {vid}")
            for _ in range(120):
                if any(m["t"] == "vmeta" for m in app.session.sent): break
                await asyncio.sleep(0.5)
            meta = [m for m in app.session.sent if m["t"] == "vmeta"][0]
            chunks = [m for m in app.session.sent if m["t"] == "vchunk"]
            assert meta["chunks"] == len(chunks), (meta, len(chunks))
            assert meta["size"] > 0 and len(meta["sha256"]) == 64
            print(f"DM /big-vid: sent vmeta size={meta['size']} in {len(chunks)} chunk(s)")

            # --- server mode
            app.session = FakeSession(); app.state = SERVER
            app.srv = {"onion":"x","name":"s","local":"s","channel":"general",
                       "channels":["general"],"admin":True,"online":[],"buffers":{},"download":None}
            await app.handle_command(f"/big-vid {vid}")
            for _ in range(120):
                if any(m["t"] == "mput" for m in app.session.sent): break
                await asyncio.sleep(0.5)
            put = [m for m in app.session.sent if m["t"] == "mput"][0]
            mch = [m for m in app.session.sent if m["t"] == "mchunk"]
            assert put["kind"] == "vid" and put["chan"] == "general"
            assert put["chunks"] == len(mch)
            print(f"server /big-vid: mput size={put['size']} in {len(mch)} chunk(s)")

            # unknown file is a clean error, not a crash
            msgs.clear()
            await app.handle_command("/big-vid /nope/missing.mp4")
            await asyncio.sleep(0.5)
            assert any("no such file" in m for m in msgs), msgs
            print("missing file handled cleanly")

            app.session = None; app.state = "idle"; app.srv = {}
            await app.action_quit()
    print("BIG-VID COMMAND TESTS PASSED")

def test_main():
    asyncio.run(main())