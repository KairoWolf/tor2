"""One 8-digit code should be the whole invitation: address and authorisation."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tor2 import proto
from tor2.serverd import JOIN_CODE_PERSON, Tor2Server
from tor2.serverdb import ServerDB
from tor2.tornet import code_to_key, onion_from_pub


def test_code_derives_a_stable_address_separate_from_pairing_codes():
    a = onion_from_pub(code_to_key("48213902", JOIN_CODE_PERSON)[1])
    again = onion_from_pub(code_to_key("48213902", JOIN_CODE_PERSON)[1])
    assert a == again, "the same code must always give the same address"

    other = onion_from_pub(code_to_key("48213903", JOIN_CODE_PERSON)[1])
    assert a != other, "different codes must give different addresses"

    pairing = onion_from_pub(code_to_key("48213902", "tor2-rendezvous-v1")[1])
    assert a != pairing, "a server code and a chat pairing code must not collide"


def test_join_codes_are_eight_digits_and_respect_uses_admin_and_expiry():
    with tempfile.TemporaryDirectory() as tmp:
        db = ServerDB(Path(tmp))
        code = db.create_join_code(uses=1)
        assert len(code) == 8 and code.isdigit(), code
        assert db.redeem_join_code(code) is False       # not admin
        assert db.redeem_join_code(code) is None, "single use was reusable"

        multi = db.create_join_code(uses=2, is_admin=True)
        assert db.redeem_join_code(multi) is True
        assert db.redeem_join_code(multi) is True
        assert db.redeem_join_code(multi) is None

        expired = db.create_join_code(ttl_seconds=-1)
        assert db.redeem_join_code(expired) is None, "expired code accepted"
        assert expired not in [e["code"] for e in db.active_join_codes()]

        live = db.create_join_code(uses=5)
        assert db.revoke_join_code(live) is True
        assert db.redeem_join_code(live) is None, "revoked code still worked"
        db.close()


def test_a_join_code_authorises_a_join_on_its_own():
    """No invite, no token — the digits are the whole credential."""
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            srv = Tor2Server(Path(tmp))
            code = srv.db.create_join_code(uses=1)
            aio = await asyncio.start_server(srv.handle_conn, "127.0.0.1", 0)
            port = aio.sockets[0].getsockname()[1]

            r, w = await asyncio.open_connection("127.0.0.1", port)
            s = await proto.handshake(r, w)
            assert (await s.recv())["t"] == "srvhello"
            await s.send({"t": "auth", "nick": "mia", "code": code})
            ok = await asyncio.wait_for(s.recv(), timeout=10)
            assert ok["t"] == "authok", ok
            assert ok["token"], "a member joining by code still gets a token"

            # the code is spent, so a second person cannot reuse it
            r2, w2 = await asyncio.open_connection("127.0.0.1", port)
            s2 = await proto.handshake(r2, w2)
            await s2.recv()
            await s2.send({"t": "auth", "nick": "bob", "code": code})
            err = await asyncio.wait_for(s2.recv(), timeout=10)
            assert err["t"] == "srverr", err

            aio.close()
            srv.db.close()
    asyncio.run(go())


def test_admin_join_code_grants_admin():
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            srv = Tor2Server(Path(tmp))
            code = srv.db.create_join_code(uses=1, is_admin=True)
            aio = await asyncio.start_server(srv.handle_conn, "127.0.0.1", 0)
            port = aio.sockets[0].getsockname()[1]
            r, w = await asyncio.open_connection("127.0.0.1", port)
            s = await proto.handshake(r, w)
            await s.recv()
            await s.send({"t": "auth", "nick": "boss", "code": code})
            ok = await asyncio.wait_for(s.recv(), timeout=10)
            assert ok["t"] == "authok" and ok["admin"] is True, ok
            aio.close()
            srv.db.close()
    asyncio.run(go())


def test_authok_reports_the_permanent_address():
    """A code's address is temporary; clients must be told the real one."""
    async def go():
        with tempfile.TemporaryDirectory() as tmp:
            srv = Tor2Server(Path(tmp))
            srv.db.set_meta("onion", "realaddress1234567890.onion")
            code = srv.db.create_join_code(uses=1)
            aio = await asyncio.start_server(srv.handle_conn, "127.0.0.1", 0)
            port = aio.sockets[0].getsockname()[1]
            r, w = await asyncio.open_connection("127.0.0.1", port)
            s = await proto.handshake(r, w)
            await s.recv()
            await s.send({"t": "auth", "nick": "mia", "code": code})
            ok = await asyncio.wait_for(s.recv(), timeout=10)
            assert ok["t"] == "authok", ok
            assert ok["address"] == "realaddress1234567890.onion", ok
            aio.close()
            srv.db.close()
    asyncio.run(go())
