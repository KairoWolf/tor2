"""Filenames, /preview, audio playback, message-id placement, work stealing."""

import asyncio
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from tor2 import media, video
from tor2.app import Tor2App
from tor2.clientserver import SERVER


class Fake:
    def __init__(self):
        self.sent = []

    async def send(self, m):
        self.sent.append(m)

    async def send_binary(self, header, blob):
        self.sent.append({**header, "bin": blob})

    async def close(self):
        pass


def server_app(app):
    app.add_conv("s", "server", "s", Fake())
    app.state = SERVER
    app.srv = {"onion": "x", "name": "s", "local": "s", "channel": "general",
               "channels": ["general"], "admin": True, "online": [],
               "buffers": {}, "download": None}


def test_filename_is_shown_not_just_the_kind():
    async def go():
        app = Tor2App()
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            server_app(app)
            named = app.render_event({"nick": "mia", "ts": 0, "id": 7,
                                      "media": {"id": 12, "kind": "vid",
                                                "size": 5_000_000,
                                                "name": "holiday.mp4"}}).plain
            unnamed = app.render_event({"nick": "mia", "ts": 0, "id": 8,
                                        "media": {"id": 13, "kind": "vid",
                                                  "size": 5_000_000}}).plain
            app.session = None
            await app.action_quit()
            return named, unnamed
    named, unnamed = asyncio.run(go())
    assert "holiday.mp4" in named, named
    assert "/preview 12" in named, named
    assert "video" in unnamed, unnamed          # falls back when unnamed


def test_message_ids_always_precede_the_nickname():
    """Locally echoed messages used to get their id appended instead."""
    async def go():
        app = Tor2App()
        app.nick = "kairo"
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            server_app(app)
            await app.server_post("mine")
            app.srv_event({"t": "event", "chan": "general", "nick": "kairo",
                           "ts": 0, "body": "mine", "media": None, "id": 50})
            app.srv_event({"t": "event", "chan": "general", "nick": "mia",
                           "ts": 0, "body": "theirs", "media": None, "id": 51})
            buf = [l.plain for l in app.srv["buffers"]["general"]]
            app.session = None
            await app.action_quit()
            return buf
    buf = asyncio.run(go())
    mine = next(l for l in buf if "mine" in l)
    theirs = next(l for l in buf if "theirs" in l)
    assert re.search(r"\[50\]", mine) and re.search(r"\[51\]", theirs)
    assert mine.index("[50]") == theirs.index("[51]"), (mine, theirs)
    assert mine.index("[50]") < mine.index("kairo")
    assert len([l for l in buf if "mine" in l]) == 1, "local echo duplicated"


def test_preview_requests_only_a_thumbnail():
    async def go():
        app = Tor2App()
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            server_app(app)
            await app.handle_command("/preview 12")
            await pilot.pause(0.1)
            sent = list(app.session.sent)
            app.session = None
            await app.action_quit()
            return sent
    sent = asyncio.run(go())
    assert sent and sent[-1] == {"t": "fetch", "id": 12, "thumb": True}, sent


def test_audio_player_is_discovered_or_reported():
    app_players = [exe for exe, _ in Tor2App.AUDIO_PLAYERS]
    assert "ffplay" in app_players and "mpv" in app_players


def test_images_are_shrunk_when_much_smaller():
    img = Image.effect_mandelbrot((1600, 1200), (-3, -2.5, 2, 2.5), 200).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    png = buf.getvalue()
    got = video.recode_image(png)
    assert got is not None, "a large PNG should shrink"
    smaller, ext = got
    assert ext == "webp" and len(smaller) < len(png) * 0.5
    # tiny images are left alone
    small = io.BytesIO()
    Image.new("RGB", (40, 40), (1, 2, 3)).save(small, "PNG")
    assert video.recode_image(small.getvalue()) is None


def test_work_stealing_covers_every_byte_once():
    plan = media.ChunkPlan(10_000_000, 1_000_000)
    taken, total = [], 0
    while (piece := plan.take()) is not None:
        taken.append(piece)
        total += piece[1] - piece[0]
    assert total == 10_000_000
    assert taken[0][0] == 0 and taken[-1][1] == 10_000_000
    # a circuit that dies hands its piece back for someone else
    plan.give_back((0, 1_000_000))
    assert plan.take() == (0, 1_000_000)


def test_resume_skips_what_already_arrived():
    have = media.RangeSet()
    have.add(0, 3_000_000)
    have.add(5_000_000, 1_000_000)
    assert have.total == 4_000_000
    assert have.missing(10_000_000) == [(3_000_000, 5_000_000),
                                        (6_000_000, 10_000_000)]
    plan = media.ChunkPlan(10_000_000, 1_000_000, done_ranges=have.as_list())
    assert plan.remaining == 6_000_000, plan.remaining
