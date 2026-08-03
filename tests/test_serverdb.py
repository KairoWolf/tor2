import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2.serverdb import ServerDB, HISTORY_PER_CHANNEL

def test_main():
    with tempfile.TemporaryDirectory() as tmp:
        db = ServerDB(Path(tmp))
        assert db.channels() == ["general"]

        # invites
        code = db.create_invite(uses=1, is_admin=True)
        assert db.redeem_invite("nope-nope-nope") is None
        assert db.redeem_invite(code) is True
        assert db.redeem_invite(code) is None, "single-use invite reusable!"
        multi = db.create_invite(uses=2)
        assert db.redeem_invite(multi) is False and db.redeem_invite(multi) is False
        assert db.redeem_invite(multi) is None
        print("invites OK")

        # members + token auth + nick dedup
        mid, tok = db.create_member("kairo", True)
        assert db.member_by_token(tok)["nick"] == "kairo"
        assert db.member_by_token("garbage") is None
        _, tok2 = db.create_member("kairo", False)
        assert db.member_by_token(tok2)["nick"] == "kairo2", "nick not deduped"
        print("members OK")

        # channels
        db.create_channel("random")
        for bad in ["Bad Caps", "has space", "x"*30, ""]:
            try:
                db.create_channel(bad); raise AssertionError(f"accepted {bad!r}")
            except ValueError: pass
        try:
            db.create_channel("random"); raise AssertionError("dup accepted")
        except ValueError: pass
        print("channels OK:", db.channels())

        # messages + pruning
        for i in range(HISTORY_PER_CHANNEL + 40):
            db.add_message("general", mid, "kairo", f"m{i}")
        rows = db.db.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        assert rows == HISTORY_PER_CHANNEL, rows
        h = db.history("general", 10)
        assert len(h) == 10 and h[-1]["body"] == f"m{HISTORY_PER_CHANNEL+39}", h[-1]
        assert h[0]["ts"] <= h[-1]["ts"], "history not chronological"
        print("messages + pruning OK")

        # media
        import hashlib
        blob = b"\x89PNG" + b"x"*5000
        sha = hashlib.sha256(blob).hexdigest()
        m = db.add_media("img", "png", blob, sha)
        assert db.media_bytes(m) == blob
        assert db.media_info(m)["size"] == len(blob)
        msg = db.add_message("general", mid, "kairo", None, m)
        assert msg["media"]["id"] == m
        assert db.media_info(None) is None and db.media_bytes(9999) is None
        print("media OK")

        # last channel cannot be deleted
        db.delete_channel("random")
        try:
            db.delete_channel("general"); raise AssertionError("deleted last channel")
        except ValueError: pass
        db.remove_member(mid)
        assert db.member_by_token(tok) is None
        print("DB TESTS PASSED")

