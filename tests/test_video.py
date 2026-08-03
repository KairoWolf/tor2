import os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2 import video
from tor2.app import fmt_size

def test_main():
    assert fmt_size(500) == "1 KB"
    assert fmt_size(300*1024) == "300 KB"
    assert fmt_size(int(1.5*1024*1024)) == "1.5 MB"
    print("fmt_size OK:", fmt_size(900*1024), fmt_size(12*1024*1024))

    # simulate ffmpeg missing: empty PATH
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "x.mp4"; p.write_bytes(b"junk")
        old = os.environ["PATH"]; os.environ["PATH"] = "/nonexistent"
        try:
            assert not video.have_ffmpeg(), "have_ffmpeg should be False"
            try:
                video.probe(p)
                raise AssertionError("should have raised")
            except ValueError as e:
                assert "not installed" in str(e), e
                print("probe without ffmpeg -> clean ValueError:", e)
            try:
                video.compress(p, Path(tmp)/"o.mp4")
                raise AssertionError("should have raised")
            except ValueError as e:
                print("compress without ffmpeg -> clean ValueError:", e)
        finally:
            os.environ["PATH"] = old
    print("VIDEO FIX TESTS PASSED")

