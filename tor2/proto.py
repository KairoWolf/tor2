"""Wire protocol: NaCl handshake, double encryption, framing, message encoding.

Three layers protect every message:

1. Tor's own onion-service encryption (transport, authenticates the listener).
2. An outer session layer: ephemeral X25519 key exchange (NaCl ``Box``) done
   in :func:`handshake` — forward secrecy plus the human-verifiable
   session fingerprint.
3. An inner tor2-only layer: XSalsa20-Poly1305 (NaCl ``SecretBox``) under a
   key derived from the handshake secret with a tor2-specific derivation
   constant, wrapping a tor2 magic tag. Generic tooling that somehow held the
   outer key still could not produce or parse tor2 frames without
   implementing this layer.
"""

import asyncio
import hashlib
import json

from nacl.public import Box, PrivateKey, PublicKey
from nacl.secret import SecretBox

MAGIC = b"TOR2\x04"          # handshake magic, protocol v4
INNER_MAGIC = b"T2I1"        # tag inside the inner cipher layer
KIND_JSON = b"J"             # payload is a JSON object
KIND_BINARY = b"B"           # JSON header + raw bytes, no base64
INNER_PERSON = b"tor2-inner-v1"

MAX_FRAME = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_AUDIO_BYTES = 200 * 1024 * 1024
MAX_VIDEO_BYTES = 60 * 1024 * 1024        # /vid — quick clips
MAX_BIG_VIDEO_BYTES = 3 * 1024 * 1024 * 1024   # /big-vid — any length
VIDEO_CHUNK = 512 * 1024
BIG_CHUNK = 2 * 1024 * 1024               # fewer round trips for huge files

# Fraction of the server's disk above which uploads are refused.
SERVER_DISK_LIMIT = 0.80


def chunk_size_for(size: int) -> int:
    return BIG_CHUNK if size > MAX_VIDEO_BYTES else VIDEO_CHUNK

# Keepalive, valid in every mode: an idle Tor stream can otherwise be closed
# by a NAT or relay with no notice.
KEEPALIVE_TYPES = {"ping", "pong"}
KEEPALIVE_INTERVAL = 25
KEEPALIVE_TIMEOUT = 150

# Direct peer-to-peer chat.
DM_TYPES = {"hello", "accept", "txt", "img", "vmeta", "vchunk"}

# Server mode. Client → server:
SERVER_C2S = {
    "auth",       # invite code or saved token + nick
    "post",       # text message to a channel
    "switch",     # change active channel
    "history",    # request recent messages for a channel (before= pages back)
    "del",        # delete a message you posted (admins: anyone's)
    "mput",       # announce a media upload (kind, size, sha256, chunks)
    "mchunk",     # a chunk of the announced upload
    "fetch",      # request a stored media blob by id
    # admin only
    "mkchan", "rmchan", "newinvite", "kick",
    "ban", "unban", "bans", "promote", "demote", "autoupdate",
}
# Server → client:
SERVER_S2C = {
    "srvhello",   # identifies this peer as a server, not a DM partner
    "authok",     # token, channel list, is_admin
    "event",      # a chat or media message in a channel
    "histbatch",  # a batch of recent messages
    "members",    # roster / presence update
    "mget",       # media download header
    "mgchunk",    # a chunk of a media download
    "deleted",    # a message id that is now gone
    "srverr",     # human-readable error
}

# The complete message surface. There is deliberately no file-transfer,
# command, or code-execution message.
ALLOWED_TYPES = DM_TYPES | SERVER_C2S | SERVER_S2C | KEEPALIVE_TYPES


class ProtocolError(Exception):
    pass


class Session:
    """An established doubly-encrypted session over one duplex socket."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 box: Box, inner: SecretBox, fingerprint: str):
        self.reader = reader
        self.writer = writer
        self.box = box
        self.inner = inner
        self.fingerprint = fingerprint
        self._send_lock = asyncio.Lock()

    async def send(self, obj: dict) -> None:
        await self._send_payload(KIND_JSON,
                                 json.dumps(obj, separators=(",", ":")).encode())

    async def send_binary(self, header: dict, blob: bytes) -> None:
        """Send raw bytes with a small JSON header.

        Media used to be base64 inside JSON, which inflated every transfer by
        a third. This carries the bytes as they are.
        """
        head = json.dumps(header, separators=(",", ":")).encode()
        await self._send_payload(
            KIND_BINARY, len(head).to_bytes(4, "big") + head + blob)

    async def _send_payload(self, kind: bytes, payload: bytes) -> None:
        inner_ct = self.inner.encrypt(INNER_MAGIC + kind + payload)
        outer_ct = self.box.encrypt(inner_ct)  # each layer: random nonce prepended
        frame = len(outer_ct).to_bytes(4, "big") + outer_ct
        async with self._send_lock:
            self.writer.write(frame)
            await self.writer.drain()

    async def recv(self) -> dict:
        header = await self.reader.readexactly(4)
        length = int.from_bytes(header, "big")
        if length == 0 or length > MAX_FRAME:
            raise ProtocolError(f"bad frame length {length}")
        outer_ct = await self.reader.readexactly(length)
        inner_ct = self.box.decrypt(outer_ct)
        tagged = self.inner.decrypt(inner_ct)
        if tagged[: len(INNER_MAGIC)] != INNER_MAGIC:
            raise ProtocolError("inner layer tag mismatch — not a tor2 peer")
        kind = tagged[len(INNER_MAGIC):len(INNER_MAGIC) + 1]
        body = tagged[len(INNER_MAGIC) + 1:]
        if kind == KIND_JSON:
            obj = json.loads(body.decode())
            blob = None
        elif kind == KIND_BINARY:
            head_len = int.from_bytes(body[:4], "big")
            if head_len > 65536 or head_len + 4 > len(body):
                raise ProtocolError("bad binary header")
            obj = json.loads(body[4:4 + head_len].decode())
            blob = body[4 + head_len:]
        else:
            raise ProtocolError("unknown payload kind")
        if not isinstance(obj, dict) or obj.get("t") not in ALLOWED_TYPES:
            raise ProtocolError("unknown message type")
        if blob is not None:
            obj["bin"] = blob
        return obj

    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


async def handshake(reader: asyncio.StreamReader,
                    writer: asyncio.StreamWriter) -> Session:
    """Exchange magic + ephemeral public keys, derive both cipher layers."""
    priv = PrivateKey.generate()
    writer.write(MAGIC + bytes(priv.public_key))
    await writer.drain()

    hello = await asyncio.wait_for(reader.readexactly(len(MAGIC) + 32), timeout=30)
    if hello[: len(MAGIC)] != MAGIC:
        raise ProtocolError("peer is not speaking the tor2 protocol (or runs an old version)")
    peer_pub = PublicKey(hello[len(MAGIC):])

    box = Box(priv, peer_pub)
    inner_key = hashlib.blake2b(
        box.shared_key(), digest_size=32, person=INNER_PERSON).digest()
    inner = SecretBox(inner_key)

    # Order-independent fingerprint of both session keys: both sides compute
    # the same value and can read it to each other to rule out a MITM.
    keys = sorted([bytes(priv.public_key), bytes(peer_pub)])
    digest = hashlib.sha256(b"tor2-fp" + keys[0] + keys[1]).hexdigest()
    fingerprint = "-".join(digest[i:i + 4] for i in range(0, 16, 4))
    return Session(reader, writer, box, inner, fingerprint)
