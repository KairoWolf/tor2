# tor2

Encrypted chat over Tor onion services, in your terminal — private
two-person conversations plus self-hosted servers with channels.
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
- **Self-hosted servers** — run a daemon on your own box and get
  Discord-style channels, invite codes, roles, and searchable history, all
  reachable only as an onion service. See [Self-hosting a server](#self-hosting-a-server).
- **Persistent nickname** — `/nick` is remembered across restarts.

## Requirements

- Linux or macOS with **Tor installed** (`pacman -S tor` / `apt install tor`
  / `brew install tor`). It does not need to run as a service — tor2 starts
  its own private instance.
- **ffmpeg** for video support (optional; everything else works without it).
- **Python 3.11+**, plus `python3-venv` on Debian/Ubuntu (it ships
  separately there).

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

## Self-hosting a server

A tor2 server is a small daemon you run on your own machine (a homelab box,
a spare Pi, anything that stays on). It publishes one **stable** onion
address and hosts Discord-style channels. There is no cloud service involved
and no port forwarding to configure.

### Install

On the machine that will host it:

```sh
git clone https://github.com/KairoWolf/tor2.git
cd tor2
./install-server.sh my-server-name
```

The script checks dependencies, creates the virtualenv, initializes
`~/.local/share/tor2-server/`, installs a **systemd user service** so the
server survives reboots, and prints:

```
  address: ukmb36ciovvnnpp5….onion
  admin invite (one use): kfpr-2xmq-nwte

  In tor2, join it with:
      /joinserver ukmb36ciovvnnpp5….onion kfpr-2xmq-nwte
```

Re-running the script is safe — it never overwrites an existing data
directory, and the onion address stays the same across restarts.

Managing it:

As **root** (a dedicated box or LXC container) it installs a system service;
as a normal user it installs a `--user` service. Manage it with whichever
scope the installer reports:

```sh
systemctl status tor2-server               # root install
systemctl --user status tor2-server        # user install
journalctl -u tor2-server -f               # live logs (add --user if applicable)

# these all work from any directory
SRV="/root/tor2/.venv/bin/tor2-server ~/.local/share/tor2-server"
$SRV --address      # the server's onion address
$SRV --info         # address, name, channels, member count
$SRV --invite       # mint another one-use invite code
```

Invite codes are single-use by default. The admin invite printed at install
time is consumed by the first person who joins, so mint a fresh code for
each additional member — either with the command above, or with
`/newinvite` (or `/newinvite 5` for a five-use code) inside tor2 as an
admin.

### Joining and using a server

```
/joinserver <onion> <invite-code> [local-name]   # first time
/server <local-name>                             # afterwards — no invite needed
```

Your membership token is saved, so rejoining is one command. Inside a
server, a sidebar lists channels and who is online, and the input bar posts
to the current channel:

| command | effect |
|---|---|
| `/ch <name>` | switch channel |
| `/channels` · `/members` | list channels / who's online |
| `/img <path>` | post an image (shown inline to everyone) |
| `/vid <path>` | post a video (auto-compressed; others download on demand) |
| `/get <id>` | download a posted video |
| `/disconnect` | return to direct-message mode |
| `/leave` | disconnect and forget this server |

Admins additionally get `/mkchan <name>`, `/rmchan <name>`, `/kick <nick>`,
and `/newinvite [uses] [admin]` to mint invite codes.

### What a server does and doesn't protect

- Members reach the server only over Tor, through the same double-encrypted
  session used for direct messages, and only with a valid invite.
- **The server operator can read channel messages.** The daemon decrypts
  them to route and store them — the same trust model as a self-hosted
  Matrix or Mattermost. Run it yourself, and use direct messages (which stay
  end-to-end encrypted between the two people) for anything you don't want
  the host to see.
- History is capped at 500 messages per channel and media at 2 GB total,
  oldest evicted first. Kicking a member revokes their token immediately.

## Safety properties

- Onion addresses are self-authenticating: connecting to the right address
  cryptographically guarantees you reached the holder of that key.
- The accept gate means knowing an address or code only lets someone
  *request* a chat; the fingerprint confirms *who* you accepted.
- The message surface is a fixed, small set of chat and channel messages —
  there is no file-transfer or command type, and nothing a peer or server
  sends can run code or touch your machine outside `./received/`. Unknown
  types and frames missing the inner tor2 layer terminate the session.
- Server members must present a valid invite or a saved token before the
  daemon will accept anything else from them; secrets are stored only as
  hashes, so a stolen server database cannot be used to log in.
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
