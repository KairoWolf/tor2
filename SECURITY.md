# What tor2 protects, and what it doesn't

Written after reading through the whole project. It says what is actually
true, including the parts that are weaker than you might assume. If you are
deciding whether to trust this with a conversation, read the
[limitations](#what-this-does-not-protect-you-from) as carefully as the rest.

**This is a hobby project, not audited software.** Nothing here has been
reviewed by anyone but its authors. Do not stake your safety on it.

---

## One-to-one chats

**End-to-end encrypted, with no server involved at all.** Three layers wrap
every message:

| layer | what it does |
|---|---|
| Tor onion service | Encrypts the connection and hides both IP addresses. An onion address *is* a public key, so reaching the right address proves you reached the right machine — no certificate authority. |
| Session layer | Fresh X25519 key exchange per session, then XSalsa20-Poly1305. New keys every time, so recording today's traffic does not help someone who steals a key tomorrow. |
| Inner layer | A second XSalsa20-Poly1305 under a key derived from the session secret with BLAKE2b and a tor2-specific constant. Anything that somehow held the outer key still could not read or forge a frame without implementing this. |

Both ends compute a **session fingerprint** from the two public keys. Reading
it aloud and finding it matches is what rules out a machine in the middle.
Nothing else does — the app cannot tell you *who* is on the other end, only
that the connection is consistent end to end.

Nobody can send you anything without your consent: an incoming chat shows the
name and fingerprint and waits for you to accept.

## Servers

Members reach a server over the same three layers, and only with a valid
invite. But:

> **The person running a server can read every message in it.** The daemon
> decrypts messages to deliver and store them. This is the same trust model as
> a self-hosted Matrix or Mattermost — run it yourself, and use one-to-one
> chats for anything the host should not see.

Beyond that:

- **Storage is encrypted at rest** with XSalsa20-Poly1305: message bodies,
  nicknames, media files and thumbnails. Media is encrypted in 1 MB chunks so a
  multi-gigabyte video still streams without being decrypted into memory, and a
  ranged request decrypts only the slice asked for.
- **Secrets are stored only as hashes.** Membership tokens and invite codes are
  kept as SHA-256 digests; a stolen database does not let anyone log in.
- **Guessing is throttled.** Ten failed invites or tokens within five minutes
  and the server stops accepting attempts for a while.
- **Flood protection**: 15 messages per 10 seconds per member, so nobody can
  push a channel's history out through the retention limit.
- **The disk cannot be filled**: uploads are refused at 80% usage.

### Where the at-rest key lives, and what that buys

| mode | protects against | does not protect against |
|---|---|---|
| **Key file** (default) — `0600` in the data directory | A stolen database, a stolen backup, media pulled off a disk image | Someone who copies the whole data directory, key included |
| **Passphrase** (`--passphrase` or `TOR2_PASSPHRASE`) — Argon2id, never written down | A copy of everything, including the disk | Someone who can read the server's memory while it runs |

The passphrase mode costs you unattended restarts: the server cannot come back
after a reboot without someone typing it.

## The code surface

The protocol carries chat and media and **nothing that can run code or read
files**. Unknown message types end the session, as do frames missing the inner
layer. Checked while writing this:

- **No shell is ever invoked** — no `shell=True`, no `os.system`, no `eval`,
  no `exec`, no `pickle` anywhere in the project.
- **ffmpeg is only ever passed local paths this program chose**, as argument
  lists rather than command strings. No value from the network reaches it.
- **Sender-supplied filenames never become paths.** They are shown as labels
  only, stripped of anything but letters, digits and a few punctuation marks;
  files on disk are named from the database id and a sanitised extension.
- **Media is validated before it is kept**: pictures must fully decode with
  PIL's decompression-bomb guard active, videos must probe as real video, and
  everything must match the SHA-256 announced for it.
- Frames are capped at 16 MB, pictures at 5 MB, videos at 3 GB.

## What this does not protect you from

- **The server operator, in a server.** Stated above; it bears repeating.
- **Whoever holds your device.** Contacts, saved servers, membership tokens and
  received files sit in your home directory. Full-disk encryption is your job.
- **Traffic analysis.** Tor hides addresses, not the fact that you are using
  it, nor the shape and timing of what you send. A server necessarily learns
  who talks to whom and when.
- **A guessed code, in principle.** A 5-digit pairing code has 100,000
  possibilities and an 8-digit join code 100 million. Reaching one still means
  finding its onion address in Tor's directory, which is slow and rate-limited,
  and a join code is single-use by default and expires — but a live code is not
  a secret you should treat as strong. Prefer full addresses if you are being
  actively targeted.
- **A malicious peer wasting your resources.** Size caps and validation limit
  the damage, but someone you accepted can still send you things you did not
  want.
- **Bugs.** Several real ones were found and fixed during development, some by
  a user rather than by the tests. Assume more remain.

## What is verified, and how

The wire format and its cryptography are covered by tests that run on every
push:

```sh
pytest tests -q
```

Those tests assert, among other things: that a frame encrypted with only the
outer layer is rejected; that key derivation matches PyNaCl byte for byte;
that nothing readable is left on disk by a server; that a spent code is
refused with a reason; and that unknown message types end a session.

## Reporting a problem

Open an issue at <https://github.com/KairoWolf/tor2>, or if it is sensitive,
say so in the issue without detail and ask for a way to send it privately.

## Lawful use only

tor2 is for legitimate private conversation. You are responsible for
complying with the laws that apply to you, including those governing
encryption and Tor. Provided as-is, with no warranty — see [LICENSE](LICENSE).
