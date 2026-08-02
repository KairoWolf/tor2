# tor2

Encrypted two-person chat over Tor onion services, in your terminal.
Text, images, and videos only — no file transfer, no remote commands, no
computer access.

> ## ⚖️ Legal use only
>
> **tor2 is intended solely for lawful use.** It is a privacy tool for
> legitimate private communication between two consenting people — journalists
> and sources, people under censorship, friends who value privacy, or anyone
> learning about Tor and cryptography.
>
> Do **not** use this software for any illegal activity. You are responsible
> for complying with all laws that apply to you, including laws governing the
> use of encryption and of the Tor network in your jurisdiction. The authors
> provide this software as-is, without warranty, and accept no responsibility
> for misuse (see [LICENSE](LICENSE)).

## Features

- **Serverless** — each side publishes an ephemeral v3 onion service; Tor
  handles reachability and NAT traversal. No account, no middleman.
- **5-digit pairing codes** — instead of dictating a 56-character onion
  address, run `/code` and have your peer type `/join 48213`. The code
  deterministically derives a temporary rendezvous onion address on both
  ends, so even the pairing step needs no server.
- **Accept gate** — nobody can start chatting with you just by knowing your
  address or code. Every incoming session shows the peer's name and the
  session fingerprint, and waits for your `/accept` or `/reject`.
- **Contacts** — `/add mia` after a chat (or `/add mia <onion>`) and next
  time it's just `/connect mia`. Stored in `~/.config/tor2/`.
- **Images & videos** — images preview inline in the terminal; videos are
  auto-compressed with ffmpeg (≤480p H.264) and sent chunked with progress.
- **Triple encryption** — Tor's onion encryption, an ephemeral X25519
  session layer with forward secrecy and a human-verifiable fingerprint, and
  an inner tor2-only XSalsa20-Poly1305 layer that only this program can
  produce or parse.
- **Persistent nickname** — `/nick` is remembered across restarts.

## Requirements

- Linux or macOS with **Tor installed** (`pacman -S tor` / `apt install tor`
  / `brew install tor`). It does not need to run as a service — tor2 starts
  its own private instance.
- **ffmpeg** for video support (optional; everything else works without it).
- **Python 3.11+**

## Install

```sh
git clone https://github.com/KairoWolf/tor2.git
cd tor2
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Use

Both people run:

```sh
.venv/bin/python -m tor2
```

Wait for Tor to bootstrap (~30–60 s on first run). Then either:

**Easiest — pairing code.** One person types `/code` and reads the 5-digit
code to the other, who types `/join <code>`. Codes are valid for 15 minutes
and retire after one use.

**Or — onion address.** Your address is in the top bar; send it over any
channel and your peer runs `/connect <address>`.

Either way, the receiving side sees:

```
• incoming chat request from “mia”
• session fingerprint: d860-7390-ab18-4808
• type /accept to start chatting, or /reject to refuse
```

After `/accept`, **verify the fingerprint** — both screens must show the
same code. Then just type to chat.

| command | effect |
|---|---|
| `/code` | publish a 5-digit pairing code (`/code off` cancels) |
| `/join <code>` | connect using a peer's pairing code |
| `/connect <contact-or-onion>` | connect directly |
| `/accept` · `/reject` | answer an incoming chat request |
| `/img <path>` | send an image (png/jpg/gif/webp/bmp, ≤5 MB) |
| `/vid <path>` | send a video (auto-compressed, ≤10 min) |
| `/add <name> [onion]` | save a contact (defaults to current peer) |
| `/contacts` · `/delcontact <name>` | list / remove contacts |
| `/nick <name>` | set your display name (persists) |
| `/disconnect` | drop the session |
| `ctrl+q` | quit — removes your onion service |

Received images and videos are validated and saved to `./received/`.

## Safety properties

- Onion addresses are self-authenticating: connecting to the right address
  cryptographically guarantees you reached the holder of that key.
- The accept gate means knowing an address or code only lets someone
  *request* a chat; the fingerprint confirms *who* you accepted.
- Only six message types exist (`hello`, `accept`, `txt`, `img`, `vmeta`,
  `vchunk`) — there is no file-transfer or command surface. Unknown types
  and frames missing the inner tor2 layer terminate the session.
- Media is validated before it is kept: images must fully decode in PIL
  (decompression-bomb guard active), videos must probe as real video via
  ffprobe and match their announced size and SHA-256.
- Frames capped at 16 MB, images 5 MB, videos 60 MB compressed, nicknames
  and contact names sanitized. Sender-supplied filenames are never used.

## Limitations to understand

- A pairing code is guessable in principle (100,000 combinations) while it
  is live — that only lets a guesser *request* a chat, which you can reject,
  but prefer full addresses if you're being actively targeted.
- Tor is slow; a 60 MB video can take many minutes to transfer.
- This is a hobby project, not audited software. Do not rely on it in
  situations where your safety depends on it.
