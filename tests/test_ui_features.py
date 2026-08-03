"""Progress readout, local echo, tab completion, settings, audio."""
import asyncio, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2.app import Tor2App
async def _no_network(self): self.set_status("x")
Tor2App.start_network = _no_network
from tor2 import settings, store, video
from tor2.clientserver import SERVER
from textual.widgets import Static

class Fake:
    def __init__(self): self.sent=[]
    async def send(self, m): self.sent.append(m)
    async def close(self): pass

async def main():
    # --- settings defaults: quiet unless asked
    assert settings.SETTINGS["notify_sound"][2] is False
    assert settings.SETTINGS["notify_all"][2] is False
    assert settings.SETTINGS["desktop_notify"][2] is False
    print("notifications default to off")

    app = Tor2App(); app.nick = "kairo"
    async with app.run_test() as pilot:
        await pilot.pause(0.3)

        # --- progress bar shows from the start and reports rate
        bar = app.query_one("#progress", Static)
        app.start_transfer("uploading", 10_000_000)
        await pilot.pause(0.1)
        assert bar.display, "bar not visible at transfer start"
        app.transfer_progress(2_500_000)
        await pilot.pause(0.1)
        txt = app.progress_text
        assert "25%" in txt, txt
        assert "MB" in txt, txt
        print("progress readout:", txt.strip())
        app.progress_done(); await pilot.pause(0.1)
        assert not bar.display
        print("bar hides only when done")

        # --- server conversation for echo/completion tests
        conv = app.add_conv("srv:t", "server", "kairos", Fake())
        app.state = SERVER
        app.srv = {"onion":"x","name":"kairos","local":"k","channel":"general",
                   "channels":["general","random","music"],"admin":True,
                   "online":["kairo","mia"],"buffers":{},"download":None}
        app.refresh_view(); await pilot.pause(0.3)

        # --- local echo: message appears before any server response
        await app.server_post("instant?")
        assert any("instant?" in getattr(l,"plain",str(l))
                   for l in app.srv["buffers"]["general"]), "no local echo"
        assert app.session.sent[-1]["t"] == "post"
        print("local echo drew the message immediately")

        # the server's echo must not duplicate it
        app.srv_event({"t":"event","chan":"general","nick":"kairo","ts":0,
                       "body":"instant?","media":None,"id":9})
        n = sum(1 for l in app.srv["buffers"]["general"]
                if "instant?" in getattr(l,"plain",str(l)))
        assert n == 1, f"message duplicated {n} times"
        print("server echo reconciled, no duplicate")

        # --- tab completion
        assert set(app.completions("/ch ")) >= {"/ch general","/ch random","/ch music"}
        assert app.completions("/ch ra") == ["/ch random"]
        assert "/big-vid" in app.completions("/big")
        assert any(c.endswith("mia") for c in app.completions("hey @m")), \
            app.completions("hey @m")
        print("tab completion: commands, channels and nicknames")

        inp = app.query_one("#inputbar")
        inp.focus(); inp.value = "/ch ra"
        await pilot.pause(0.1)
        await pilot.press("tab"); await pilot.pause(0.2)
        assert inp.value.strip() == "/ch random", repr(inp.value)
        print("pressing tab completed to:", inp.value.strip())

        # --- settings screen opens and persists a change
        await app.handle_command("/settings")
        await pilot.pause(0.5)
        assert app.screen is not app.screen_stack[0], "settings screen did not open"
        settings.save("notify_sound", True)
        app.apply_settings()
        assert settings.get("notify_sound") is True
        assert store.load_config().get("notify_sound") is True, "not persisted"
        settings.save("notify_sound", False)
        app.pop_screen(); await pilot.pause(0.3)
        print("settings screen opens and persists")

        await app.action_quit()

    # --- audio pipeline
    if video.have_ffmpeg():
        with tempfile.TemporaryDirectory() as t:
            t = Path(t); src = t/"tone.wav"
            subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i",
                            "sine=frequency=440:duration=3", str(src)], check=True)
            info = video.probe_audio(src)
            assert 2.5 < info["duration"] < 3.5, info
            out = t/"out.mp3"
            seen=[]
            video.compress_audio(src, out, on_progress=seen.append,
                                 duration=info["duration"])
            assert out.stat().st_size > 0 and seen and seen[-1] == 1.0
            video.validate_audio(out)
            print(f"audio: {src.stat().st_size//1024} KB wav -> "
                  f"{out.stat().st_size//1024} KB mp3, {len(seen)} progress updates")
            try:
                video.probe_audio(t/"nope.wav"); raise AssertionError("accepted junk")
            except ValueError: pass
            print("non-audio rejected")
    print("BATCH A TESTS PASSED")

def test_main():
    asyncio.run(main())