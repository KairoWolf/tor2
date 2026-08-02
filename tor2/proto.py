"""Wire protocol: NaCl handshake, framing, and message encoding.

Layered on top of the Tor onion connection (which is already encrypted and
authenticates the *server* side via its onion address). This layer adds a
session-ephemeral X25519 key exchange so both directions get forward secrecy
and a mutual fingerprint the two humans can compare out loud.
"""

import asyncio
import hashlib
import json

from nacl.public import Box, PrivateKey, PublicKey

MAGIC = b"TOR2\x01"
MAX_FRAME = 16 * 1024 * 1024  # hard cap on a single encrypted frame
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Only these message types are ever acted on. There is deliberately no
# file-transfer, command, or code-execution message.
ALLOWED_TYPES = {"hello", "txt", "img"}


class ProtocolError(Exception):
    pass


class Session:
    """An established encrypted session over one duplex socket."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 box: Box, fingerprint: str):
        self.reader = reader
        self.writer = writer
        self.box = box
        self.fingerprint = fingerprint
        self._send_lock = asyncio.Lock()

    async def send(self, obj: dict) -> None:
        plaintext = json.dumps(obj, separators=(",", ":")).encode()
        ciphertext = self.box.encrypt(plaintext)  # random nonce prepended
        frame = len(ciphertext).to_bytes(4, "big") + ciphertext
        async with self._send_lock:
            self.writer.write(frame)
            await self.writer.drain()

    async def recv(self) -> dict:
        header = await self.reader.readexactly(4)
        length = int.from_bytes(header, "big")
        if length == 0 or length > MAX_FRAME:
            raise ProtocolError(f"bad frame length {length}")
        ciphertext = await self.reader.readexactly(length)
        plaintext = self.box.decrypt(ciphertext)
        obj = json.loads(plaintext.decode())
        if not isinstance(obj, dict) or obj.get("t") not in ALLOWED_TYPES:
            raise ProtocolError("unknown message type")
        return obj

    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


async def handshake(reader: asyncio.StreamReader,
                    writer: asyncio.StreamWriter) -> Session:
    """Exchange magic + ephemeral public keys, derive the session box."""
    priv = PrivateKey.generate()
    writer.write(MAGIC + bytes(priv.public_key))
    await writer.drain()

    hello = await asyncio.wait_for(reader.readexactly(len(MAGIC) + 32), timeout=30)
    if hello[: len(MAGIC)] != MAGIC:
        raise ProtocolError("peer is not speaking the tor2 protocol")
    peer_pub = PublicKey(hello[len(MAGIC):])

    box = Box(priv, peer_pub)
    # Order-independent fingerprint of both session keys: both sides compute
    # the same value and can read it to each other to rule out a MITM at the
    # local Tor daemon.
    keys = sorted([bytes(priv.public_key), bytes(peer_pub)])
    digest = hashlib.sha256(b"tor2-fp" + keys[0] + keys[1]).hexdigest()
    fingerprint = "-".join(digest[i:i + 4] for i in range(0, 16, 4))
    return Session(reader, writer, box, fingerprint)
