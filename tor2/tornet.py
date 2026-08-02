"""Tor process management: private tor instance, ephemeral onion, SOCKS5 dialing."""

import asyncio
import base64
import hashlib
import re
import socket
from pathlib import Path

from nacl import bindings
import stem.process
from stem.control import Controller

ONION_RE = re.compile(r"^[a-z2-7]{56}(\.onion)?$")


def code_to_key(code: str) -> tuple[bytes, bytes]:
    """Derive a deterministic ed25519 keypair from a pairing code.

    Both peers compute the same key from the same code, so the code alone
    determines a temporary onion address — no lookup server needed.
    Returns (expanded_secret_64B_for_tor, public_key_32B).
    """
    seed = hashlib.blake2b(f"tor2-rendezvous-v1:{code}".encode(),
                           digest_size=32).digest()
    h = bytearray(hashlib.sha512(seed).digest())
    h[0] &= 248
    h[31] &= 127
    h[31] |= 64
    expanded = bytes(h)
    pub = bindings.crypto_scalarmult_ed25519_base_noclamp(expanded[:32])
    return expanded, pub


def onion_from_pub(pub: bytes) -> str:
    """Compute the v3 onion address for an ed25519 public key."""
    version = b"\x03"
    checksum = hashlib.sha3_256(b".onion checksum" + pub + version).digest()[:2]
    return base64.b32encode(pub + checksum + version).decode().lower() + ".onion"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def normalize_onion(addr: str) -> str:
    addr = addr.strip().lower()
    addr = addr.removeprefix("http://").removeprefix("https://").rstrip("/")
    if not ONION_RE.match(addr):
        raise ValueError("that doesn't look like a v3 onion address")
    return addr.removesuffix(".onion") + ".onion"


class TorNet:
    """Owns a private tor process for the lifetime of the chat session."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.socks_port = _free_port()
        self.control_port = _free_port()
        self.process = None
        self.controller = None
        self.onion_addr: str | None = None

    def launch(self, on_progress=None) -> None:
        """Blocking: start tor and wait for bootstrap. Run in a thread."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.chmod(0o700)

        def handle_line(line: str) -> None:
            if on_progress and "Bootstrapped" in line:
                m = re.search(r"Bootstrapped (\d+)%", line)
                if m:
                    on_progress(int(m.group(1)))

        self.process = stem.process.launch_tor_with_config(
            config={
                "SocksPort": f"127.0.0.1:{self.socks_port}",
                "ControlPort": f"127.0.0.1:{self.control_port}",
                "DataDirectory": str(self.data_dir),
                "CookieAuthentication": "1",
            },
            init_msg_handler=handle_line,
            take_ownership=True,
            # no timeout: stem's timeout uses signal.alarm, which is
            # main-thread-only, and we launch from a worker thread
        )
        self.controller = Controller.from_port(port=self.control_port)
        self.controller.authenticate()

    def create_onion(self, local_port: int) -> str:
        """Blocking: publish an ephemeral v3 onion → 127.0.0.1:local_port."""
        service = self.controller.create_ephemeral_hidden_service(
            {80: f"127.0.0.1:{local_port}"},
            key_type="NEW",
            key_content="ED25519-V3",
            await_publication=True,
        )
        self.onion_addr = service.service_id + ".onion"
        return self.onion_addr

    def publish_code_onion(self, code: str, local_port: int) -> str:
        """Blocking: publish the rendezvous onion derived from a pairing code."""
        expanded, pub = code_to_key(code)
        service = self.controller.create_ephemeral_hidden_service(
            {80: f"127.0.0.1:{local_port}"},
            key_type="ED25519-V3",
            key_content=base64.b64encode(expanded).decode(),
            await_publication=True,
        )
        expected = onion_from_pub(pub)
        actual = service.service_id + ".onion"
        if actual != expected:  # sanity: our derivation must match tor's
            self.controller.remove_ephemeral_hidden_service(service.service_id)
            raise RuntimeError("rendezvous key derivation mismatch")
        self.code_service_id = service.service_id
        return actual

    def remove_code_onion(self) -> None:
        sid = getattr(self, "code_service_id", None)
        if sid and self.controller:
            try:
                self.controller.remove_ephemeral_hidden_service(sid)
            except Exception:
                pass
        self.code_service_id = None

    async def dial(self, onion: str, port: int = 80):
        """Connect to a peer's onion service through our SOCKS5 port."""
        reader, writer = await asyncio.open_connection("127.0.0.1", self.socks_port)
        try:
            # SOCKS5 greeting, no auth
            writer.write(b"\x05\x01\x00")
            await writer.drain()
            resp = await reader.readexactly(2)
            if resp != b"\x05\x00":
                raise ConnectionError("SOCKS5 handshake refused")
            # CONNECT by domain name (tor resolves .onion itself)
            host = onion.encode()
            writer.write(b"\x05\x01\x00\x03" + bytes([len(host)]) + host
                         + port.to_bytes(2, "big"))
            await writer.drain()
            resp = await reader.readexactly(4)
            if resp[1] != 0x00:
                raise ConnectionError(f"tor could not reach the peer (SOCKS code {resp[1]})")
            # drain the bound-address field
            atyp = resp[3]
            if atyp == 0x01:
                await reader.readexactly(4 + 2)
            elif atyp == 0x03:
                n = (await reader.readexactly(1))[0]
                await reader.readexactly(n + 2)
            elif atyp == 0x04:
                await reader.readexactly(16 + 2)
            return reader, writer
        except Exception:
            writer.close()
            raise

    def shutdown(self) -> None:
        try:
            if self.controller:
                self.controller.close()
        except Exception:
            pass
        try:
            if self.process:
                self.process.terminate()
                self.process.wait(timeout=10)
        except Exception:
            pass
