"""Encryption at rest: nothing readable on disk, and it still all works."""
import asyncio, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2 import atrest, media, proto
from tor2.serverd import Tor2Server
from tor2.serverdb import ServerDB

SECRET = "meet me at the usual place"

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp); data = tmp/"srv"; data.mkdir()

        srv = Tor2Server(data)
        assert srv.vault.enabled, "encryption at rest should be on by default"
        inv = srv.db.create_invite(uses=1, is_admin=True)
        aio = await asyncio.start_server(srv.handle_conn, "127.0.0.1", 0)
        port = aio.sockets[0].getsockname()[1]
        r,w = await asyncio.open_connection("127.0.0.1", port)
        s = await proto.handshake(r,w); await s.recv()
        await s.send({"t":"auth","nick":"kairo","invite":inv})
        await s.recv(); await s.recv()

        await s.send({"t":"post","chan":"general","body":SECRET})
        ev = None
        for _ in range(40):
            m = await asyncio.wait_for(s.recv(), timeout=10)
            if m["t"]=="event": ev=m; break
        assert ev and ev["body"] == SECRET, ev
        print("message round-trips through an encrypted server")

        # upload an image so there is a media file on disk
        blob = os.urandom(3*1024*1024)
        sha = __import__("hashlib").sha256(blob).hexdigest()
        f = tmp/"pic.bin"; f.write_bytes(blob)
        await media.send_file(s, f, {"t":"mput","kind":"vid","ext":"mp4",
                                     "chan":"general"}, "mchunk",
                              proto.chunk_size_for(len(blob)), sha)
        for _ in range(120):
            rows = srv.db.db.execute("SELECT id,path FROM media").fetchall()
            if rows: break
            await asyncio.sleep(0.5)
        assert rows, "upload never stored"
        stored = Path(rows[0]["path"])
        print("media stored at", stored.name)

        # --- the important part: what a thief would find
        raw_db = (data/"server.db").read_bytes()
        assert SECRET.encode() not in raw_db, "message body readable in the database!"
        print("database contains no plaintext message body")
        raw_media = stored.read_bytes()
        assert raw_media[:4] == b"T2R1", "media file is not encrypted"
        assert blob[:4096] not in raw_media, "media plaintext found on disk!"
        print("media file on disk is ciphertext")

        # --- and it can still be served back byte-exactly
        await s.send({"t":"fetch","id":rows[0]["id"]})
        got = bytearray(); hdr=None
        while len(got) < len(blob):
            m = await asyncio.wait_for(s.recv(), timeout=60)
            if m["t"]=="mget": hdr=m
            elif m["t"]=="mgchunk" and m.get("bin") is not None:
                got += m["bin"]
        assert bytes(got) == blob, "served media differs from what was uploaded"
        print("encrypted media served back byte-exactly")

        # ranged fetch through the decryption layer
        await s.send({"t":"fetch","id":rows[0]["id"],
                      "start":1_000_000,"end":1_500_000})
        part = bytearray()
        while len(part) < 500_000:
            m = await asyncio.wait_for(s.recv(), timeout=60)
            if m["t"]=="mgchunk" and m.get("bin") is not None:
                part += m["bin"]
        assert bytes(part) == blob[1_000_000:1_500_000], "ranged decrypt wrong"
        print("ranged fetch decrypts the right slice")

        aio.close(); srv.db.close()

        # --- without the key, the data is unreadable
        keyfile = data/atrest.KEY_FILE
        stashed = keyfile.read_bytes(); keyfile.unlink()
        locked = ServerDB(data, vault=atrest.Vault(None))
        try:
            locked.history("general")
            raise AssertionError("read message bodies without the key!")
        except atrest.LockedError as e:
            print("without the key file:", e)
        locked.close()
        keyfile.write_bytes(stashed)

        # --- with a passphrase instead, no key material is written
        data2 = tmp/"srv2"; data2.mkdir()
        k1 = atrest.load_key(data2, "correct horse battery staple")
        k2 = atrest.load_key(data2, "correct horse battery staple")
        k3 = atrest.load_key(data2, "wrong passphrase")
        assert k1 == k2 and k1 != k3, "passphrase derivation is not stable"
        assert not (data2/atrest.KEY_FILE).exists(), "passphrase mode wrote a key file!"
        print("passphrase mode: stable key, nothing written to disk")
    print("AT-REST TESTS PASSED")

def test_main():
    asyncio.run(main())