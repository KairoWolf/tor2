"""Encryption at rest for a server's stored data.

Message bodies, nicknames and media files are encrypted on disk with
XSalsa20-Poly1305, so a copied disk, a stolen backup or a snapshotted
container yields ciphertext rather than everyone's conversations.

The key has to be available for the server to serve anything, so where it
lives decides what this protects against:

* **key file** (default) — a 0600 file in the data directory. Protects a
  stolen database, a stolen backup, or media pulled off a disk image without
  the key file. Does not protect a full copy of the data directory.
* **passphrase** (``TOR2_PASSPHRASE``, or ``--passphrase`` to be prompted) —
  the key is derived with Argon2id and never written down. Protects a full
  copy of everything, at the price of the server not restarting unattended.

Both are honest about their limits; the README says so plainly.
"""

import hashlib
import os
from pathlib import Path

import nacl.pwhash
import nacl.utils
from nacl.secret import SecretBox

KEY_FILE = "atrest.key"
MAGIC = b"T2R1"                 # marks an encrypted value
SALT_FILE = "atrest.salt"
CHUNK = 1024 * 1024             # media files are encrypted chunk by chunk


class LockedError(Exception):
    """The stored data is encrypted and the right key was not supplied."""


class Vault:
    """Encrypts and decrypts values and files for one server."""

    def __init__(self, key: bytes | None):
        self.box = SecretBox(key) if key else None

    @property
    def enabled(self) -> bool:
        return self.box is not None

    # ---------- values ----------

    def seal(self, value: str | None) -> bytes | str | None:
        if value is None or self.box is None:
            return value
        return MAGIC + self.box.encrypt(value.encode())

    def open(self, value):
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)) and bytes(value[:4]) == MAGIC:
            if self.box is None:
                raise LockedError("this data is encrypted — start the server "
                                  "with the same key or passphrase")
            return self.box.decrypt(bytes(value[4:])).decode()
        return value if isinstance(value, str) else bytes(value).decode()

    # ---------- files ----------

    def seal_file(self, src: Path, dest: Path) -> None:
        """Encrypt chunk by chunk, so a 3 GB video never lands in memory."""
        if self.box is None:
            os.replace(src, dest)
            return
        with src.open("rb") as fin, dest.open("wb") as fout:
            fout.write(MAGIC)
            while chunk := fin.read(CHUNK):
                sealed = self.box.encrypt(chunk)
                fout.write(len(sealed).to_bytes(4, "big"))
                fout.write(sealed)
        src.unlink(missing_ok=True)

    def open_file_iter(self, path: Path, start: int = 0, end: int | None = None):
        """Yield plaintext bytes for [start, end) without decrypting the rest
        into memory. Falls back to plain reads for unencrypted files."""
        with path.open("rb") as f:
            head = f.read(4)
            if head != MAGIC:
                f.seek(start)
                remaining = None if end is None else max(0, end - start)
                while True:
                    want = CHUNK if remaining is None else min(CHUNK, remaining)
                    if want <= 0:
                        return
                    data = f.read(want)
                    if not data:
                        return
                    if remaining is not None:
                        remaining -= len(data)
                    yield data
                return
            if self.box is None:
                raise LockedError("this media is encrypted — start the server "
                                  "with the same key or passphrase")
            pos = 0
            while True:
                header = f.read(4)
                if len(header) < 4:
                    return
                n = int.from_bytes(header, "big")
                sealed = f.read(n)
                if len(sealed) < n:
                    return
                plain = self.box.decrypt(sealed)
                chunk_start, chunk_end = pos, pos + len(plain)
                pos = chunk_end
                if end is not None and chunk_start >= end:
                    return
                if chunk_end <= start:
                    continue
                lo = max(0, start - chunk_start)
                hi = len(plain) if end is None else min(len(plain), end - chunk_start)
                yield plain[lo:hi]

    def plaintext_size(self, path: Path, stored_size: int) -> int:
        return stored_size


# ---------- key management ----------

def load_key(data_dir: Path, passphrase: str | None) -> bytes | None:
    """Return the at-rest key, creating a key file on first use.

    A passphrase always wins and is never written to disk.
    """
    data_dir.mkdir(parents=True, exist_ok=True)   # may be the very first run
    data_dir.chmod(0o700)
    if passphrase:
        salt_path = data_dir / SALT_FILE
        if salt_path.is_file():
            salt = salt_path.read_bytes()
        else:
            salt = nacl.utils.random(nacl.pwhash.argon2id.SALTBYTES)
            salt_path.write_bytes(salt)
            salt_path.chmod(0o600)
        return nacl.pwhash.argon2id.kdf(
            SecretBox.KEY_SIZE, passphrase.encode(), salt,
            opslimit=nacl.pwhash.argon2id.OPSLIMIT_MODERATE,
            memlimit=nacl.pwhash.argon2id.MEMLIMIT_MODERATE)

    key_path = data_dir / KEY_FILE
    if key_path.is_file():
        key = key_path.read_bytes()
        if len(key) != SecretBox.KEY_SIZE:
            raise ValueError(f"{key_path} is not a valid key")
        return key
    key = nacl.utils.random(SecretBox.KEY_SIZE)
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    return key


def key_fingerprint(key: bytes | None) -> str:
    if not key:
        return "none"
    return hashlib.sha256(b"tor2-atrest" + key).hexdigest()[:12]
