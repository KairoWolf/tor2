# tor2

**Private chat in your terminal, over Tor.** Talk one to one with nobody in
the middle, or run your own server with Discord-style channels on a machine
you control. No accounts, no phone numbers, no company holding your messages.

<p align="center">
  <a href="https://github.com/KairoWolf/tor2/actions/workflows/tests.yml">
    <img alt="tests" src="https://github.com/KairoWolf/tor2/actions/workflows/tests.yml/badge.svg">
  </a>
</p>

```
 kairos-server  ·  #general · 3 online
┌──────────────┬────────────────────────────────────────────────┐
│ chats        │ 14:02 [41] mia   ▸ did the build finish?       │
│ ▣ kairos     │ 14:02 [42] kairo ▸ yeah, pushed it             │
│ ▸ mia    2   │ 14:03 [43] zoe   ▸ [holiday.mp4 · 82 MB]       │
│ ▸ bob    @   │             ▀▀▀▀▀▀▀▀▀▀▀▀  /get 12              │
│              │             ▀▀▀▀▀▀▀▀▀▀▀▀                       │
│ kairos       │                                                │
│ ▸ #general   │                                                │
│   #random 3  │                                                │
│ online       │                                                │
│  kairo  mia  │                                                │
└──────────────┴────────────────────────────────────────────────┘
 uploading ▕████████████░░░░░░░░░░░░░░░░▏ 43%  1.2 MB/s · 4m left
 message…  (/help for commands)
```

Several conversations run at once — a server and any number of direct chats —
with unread counts, `@` when someone mentions you, and previews for pictures
and video.

> ### ⚖️ Lawful use only
> tor2 is a privacy tool for legitimate private conversation. **Do not use it
> for anything illegal.** You are responsible for the laws that apply to you,
> including those governing encryption and Tor. Provided as-is, with no
> warranty and no responsibility for misuse — see [LICENSE](LICENSE).

---

## Contents

1. [Install](#1-install)
2. [Chat with one person](#2-chat-with-one-person)
3. [Run a server](#3-run-a-server)
4. [Command reference](#4-command-reference)
5. [Privacy: what is and isn't protected](#5-privacy-what-is-and-isnt-protected)
6. [Troubleshooting](#6-troubleshooting)
7. [Development](#7-development)

---

## 1. Install

You need **Tor**, **Python 3.11+**, and optionally **ffmpeg** for video and
audio.

```sh
# Debian / Ubuntu
sudo apt install -y tor git python3-venv ffmpeg

# Arch
sudo pacman -S tor git python ffmpeg

# macOS
brew install tor git python ffmpeg
```

```sh
git clone https://github.com/KairoWolf/tor2.git
cd tor2
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run it any time with:

```sh
cd ~/tor2
.venv/bin/python -m tor2
```

The first start takes 30–60 seconds while Tor connects; your address then
appears in the top bar. Tor does **not** need to be running as a service —
tor2 starts its own copy and shuts it down when you quit.

---

## 2. Chat with one person

No server is involved and nobody else can read it. Both people open tor2, then:

**With a 5-digit code (easiest).** One of you types `/code` and reads out the
five digits. The other types `/join 48213`.

**Or with an address.** Send the `.onion` address from your top bar over any
channel; the other person runs `/connect <address>`.

Either way the receiving side decides:

```
• incoming chat request from "mia"
• session fingerprint: d860-7390-ab18-4808
• type /accept to start chatting, or /reject to refuse
```

**Check the fingerprint matches on both screens.** That is what rules out
someone in the middle — nothing else does.

Afterwards save them, so next time is one command:

```
/add mia            then:   /connect mia
```

---

## 3. Run a server

A server is a small daemon on a machine you own — a homelab box, a spare Pi, a
VPS. It has one permanent address and hosts channels.

### Set it up

```sh
git clone https://github.com/KairoWolf/tor2.git
cd tor2
./install-server.sh my-server-name
```

That installs everything, sets it to start on boot, and prints the address and
a one-use admin invite. Run as **root** it installs a system service; as an
ordinary user, a user service. Re-running is safe: it never touches existing
data, and the address never changes.

### Invite people — one 8-digit code

```sh
tor2-server --joincode                    # one use, 24h
tor2-server --joincode 100 --hours 8760   # one code for everyone, a year
tor2-server --joincode --admin
tor2-server --codes                       # what is live
```

`install-server.sh` puts that `tor2-server` command on your PATH
(`/usr/local/bin` for a root install, `~/.local/bin` otherwise) with the data
directory already filled in. If it is missing — an older install, or a PATH
that does not include those — call the real thing instead, from the repo:
`./.venv/bin/tor2-server <data-dir> --joincode`.

You give them **only the digits**. They type `/join 48213902` and they are in.
The number derives a temporary onion address which the server publishes for
that code, so nothing is looked up anywhere; once the code is spent, that
address comes down.

**Uses max out at 100** — ask for more and it is quietly clamped. The expiry is
not capped, so one code can serve a group of up to a hundred for as long as you
like. Understand the trade: a long-lived code is a weaker secret than a
single-use one — anyone it is forwarded to can join for as long as it lives,
and the only way to take it back is `/joincode revoke <code>` from inside the
app as an admin, which kills it for everybody at once.

`--codes` also tells you whether a server is actually running to publish
them — a code cannot work otherwise.

<details>
<summary>The manual way, with an address and invite</summary>

```
/newinvite            one code
/newinvite 5          one code five people can use
```

Then `/joinserver <address> <invite>`.
</details>

### Look after it

```sh
systemctl status tor2-server                     # --user if installed as a user
journalctl -u tor2-server -f                     # live logs
tor2-server --info    # address, channels, disk
tor2-server --backup ~/backups
```

**Take backups.** The archive holds the database, stored media *and the onion
identity key* — without it, a dead machine means a new address and everyone
re-inviting. Treat the file as carefully as the server itself.

### Auto-updating (optional, off by default)

```
/autoupdate on      check daily and restart onto new versions
/autoupdate now     check once, right now
/autoupdate off     stop (the default)
```

It only ever fast-forwards, refuses if the checkout has local edits or a
different remote, and never merges or resets. It is still code fetched from
the internet and run — enable it only if you trust that repository completely.

---

## 4. Command reference

### Anywhere

| command | what it does |
|---|---|
| `/help` | commands for wherever you are |
| `/nick <name>` | your display name (remembered) |
| `/settings` | notifications, theme, previews, quality, transfer streams |
| `/update` | fetch the latest version from GitHub |
| **tab** | complete a command, channel or nickname |
| **ctrl+n / ctrl+p** | switch between open conversations |
| `/disconnect` · `ctrl+q` | leave the conversation · quit |

### One to one

| command | what it does |
|---|---|
| `/code` · `/join <5 digits>` | pair with a short code |
| `/connect <name-or-address>` | connect to a contact or address |
| `/accept` · `/reject` | answer an incoming request |
| `/add <name>` · `/contacts` · `/delcontact <name>` | save and manage contacts |

### On a server

| command | what it does |
|---|---|
| `/join <8 digits>` | join with a code alone |
| `/joinserver <address> <invite>` | join the manual way |
| `/server [name]` | reconnect a saved server, or list them |
| **click** or **↑ / ↓** · `/ch <name>` | switch channel |
| `/channels` · `/members` | what is here, who is here |
| `/more` | load older messages |
| `/del <id>` | delete your message (admins: anyone's) |
| `/leave` | disconnect and forget this server |

### Pictures, video and audio

| command | what it does |
|---|---|
| `/img <path>` | send a picture — previews for everyone automatically |
| `/vid <path>` | send a video, auto-compressed (up to 10 min) |
| `/big-vid <path>` | send a long video — any length, up to 3 GB |
| `/audio <path>` | send music or a recording (converted to mp3) |
| `/get <id>` · `/preview <id>` | download it · see a still first |
| `/save [id]` · `/play [id]` | keep a picture · replay a GIF or play audio |

### Admin

| command | what it does |
|---|---|
| `/joincode [uses] [admin] [12h]` | mint an 8-digit code (`list`, `revoke <code>`) |
| `/newinvite [uses] [admin]` | mint an invite code |
| `/mkchan <name>` · `/rmchan <name>` | create · delete a channel |
| `/kick <nick>` · `/ban <nick> [reason]` · `/unban` · `/bans` | moderation |
| `/promote <nick>` · `/demote <nick>` | grant or remove admin |
| `/autoupdate on\|off\|now` | self-updating from GitHub |

---

## 5. Privacy: what is and isn't protected

**One-to-one chats are end to end encrypted and involve no server.** Three
layers: Tor's onion encryption — which hides both IP addresses and proves you
reached the right machine, since an onion address *is* a public key — a fresh
X25519 session key each time, and a tor2-only inner cipher. The **fingerprint**
you read aloud is what catches an impostor.

**In a server, the operator can read the messages.** The daemon has to decrypt
them to deliver and store them. Run it yourself, and keep anything the host
should not see in a one-to-one chat.

Also true: server storage is **encrypted at rest**, secrets are kept only as
hashes, joining is throttled against guessing, media is validated before it is
kept, and the protocol carries **nothing that can run code or read files** — no
shell is ever invoked, and a sender's filename never becomes a path.

**[SECURITY.md](SECURITY.md) sets this out properly**, including the parts that
are weaker than you might assume: traffic analysis, guessable codes, what the
at-rest key does and does not buy you, and the plain fact that this is a hobby
project rather than audited software.

### Speed, since Tor is slow

Media travels as raw binary frames rather than base64 (25% fewer bytes), large
files are split across several Tor circuits at once, and circuits take work
from a shared queue so one slow relay cannot hold up a transfer. Pictures are
re-encoded to WebP before sending — typically 80% smaller. H.265 is available
in `/settings`, measured about 22% smaller than H.264 at matching quality.

---

## 6. Troubleshooting

**"peer is not speaking the tor2 protocol (or runs an old version)"**
Both sides need the same version. Run `/update` (or `git pull`) and restart.

**A join code does not work**
Run `tor2-server --codes` on the server. A code only works once a
running server publishes the address it derives — if the daemon is stopped or
outdated, it says so. Fix with `systemctl restart tor2-server`.

**"invalid or used-up invite code"**
Codes are single use by default. Mint another, or `--joincode 5` for one
several people can use.

**Stuck on "bootstrapping tor"**
The first connection can take a couple of minutes on a slow link. Check `tor`
is installed with `tor --version`.

**A conversation dropped**
It reconnects by itself with backoff. `/server <name>` forces a retry.

**The installer cannot create a virtualenv**
Debian and Ubuntu ship it separately: `apt install -y python3-venv`.

**Video is rejected as "not a readable video"**
ffmpeg is not installed. Text and pictures work without it.

**A server command says it needs a server**
Connect first with `/server <name>`. To mint a code while sitting on the
server box, use `tor2-server --joincode` instead.

---

## 7. Development

```sh
.venv/bin/pip install -e . pytest pytest-timeout
.venv/bin/pytest tests -q          # protocol, server, UI, crypto, transfers
```

The suite runs in CI on every push and never touches the network or your real
config. [CONTRIBUTING.md](CONTRIBUTING.md) explains what each covers, and the
main convention: every bug fix gets a test that fails before it.
