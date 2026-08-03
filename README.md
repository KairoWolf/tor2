# tor2

**Private chat in your terminal, over Tor.** Talk one-to-one, or run your own
server with Discord-style channels. No accounts, no phone numbers, no company
in the middle — just two computers finding each other through the Tor network.

```
 kairos  ·  #general · 3 online
┌──────────────┬────────────────────────────────────────────────┐
│ chats        │ 14:02 [41] mia   ▸ did the build finish?       │
│ ▣ kairos     │ 14:02 [42] kairo ▸ yeah, pushed it             │
│ ▸ mia    2   │ 14:03 [43] zoe   ▸ [video · 82.1 MB] /get 12   │
│ ▸ bob    @   │             ▀▀▀▀▀▀▀▀▀▀▀▀  (preview)            │
│              │             ▀▀▀▀▀▀▀▀▀▀▀▀                       │
│ kairos       │                                                │
│ ▸ #general   │                                                │
│   #random 3  │                                                │
│              │                                                │
│ online       │                                                │
│  kairo  mia  │                                                │
└──────────────┴────────────────────────────────────────────────┘
 uploading ▕████████████░░░░░░░░░░░░░░░░▏  43%
 message…  (/help for commands)
```

Several conversations run at once — a server and any number of direct chats
— with unread counts, `@` when someone mentions you, and inline previews for
images and videos.

> ### ⚖️ Lawful use only
> tor2 is a privacy tool for legitimate private conversation. **Do not use it
> for anything illegal.** You are responsible for following the laws that
> apply to you, including any governing encryption and Tor. Provided as-is,
> with no warranty and no responsibility for misuse — see [LICENSE](LICENSE).

---

## Contents

1. [Install](#1-install)
2. [Chat with one person](#2-chat-with-one-person)
3. [Run a server](#3-run-a-server)
4. [Command reference](#4-command-reference)
5. [How it keeps things private](#5-how-it-keeps-things-private)
6. [Troubleshooting](#6-troubleshooting)
7. [Development](#7-development)

---

## 1. Install

You need **Tor**, **Python 3.11+**, and optionally **ffmpeg** for video.

```sh
# Debian / Ubuntu
sudo apt install -y tor git python3-venv ffmpeg

# Arch
sudo pacman -S tor git python ffmpeg

# macOS
brew install tor git python ffmpeg
```

Then:

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

The first start takes 30–60 seconds while Tor connects. When it's ready your
own address appears in the top bar. Later starts are quicker.

> Tor does **not** need to be running as a service — tor2 launches its own
> private copy and shuts it down when you quit.

---

## 2. Chat with one person

Both people open tor2. Then pick whichever is easier:

**Option A — a 5-digit code (easiest).** One of you types:

```
/code
```

You get something like `48213`. Read it to the other person, who types:

```
/join 48213
```

**Option B — the full address.** Send the `.onion` address from your top bar
over any channel, and the other person runs `/connect <address>`.

Either way the receiver sees a request and decides:

```
• incoming chat request from "mia"
• session fingerprint: d860-7390-ab18-4808
• type /accept to start chatting, or /reject to refuse
```

**Check the fingerprint matches on both screens**, then chat. Nobody can talk
to you without you accepting first.

Afterwards, save them so you never need the address again:

```
/add mia          then next time just:   /connect mia
```

---

## 3. Run a server

A server is a small program on a machine you own — a homelab box, a spare Pi,
a VPS. It has one permanent address and hosts channels.

### Set it up

On the server machine:

```sh
git clone https://github.com/KairoWolf/tor2.git
cd tor2
./install-server.sh my-server-name
```

That's it. The script installs everything, sets it to start on boot, and
prints:

```
address:  ukmb36ciovvnnpp5….onion
admin invite (one use):  kfpr-2xmq-nwte

In tor2, join it with:
    /joinserver ukmb36ciovvnnpp5….onion kfpr-2xmq-nwte
```

Run as **root** it installs a system service; as a normal user, a user
service. Re-running is safe — it never touches existing data, and the address
never changes.

### Invite people

Invite codes are **single use**, so each person needs their own. As an admin
inside tor2:

```
/newinvite            one code
/newinvite 5          one code five people can use
```

Or on the server machine:

```sh
/root/tor2/.venv/bin/tor2-server ~/.local/share/tor2-server --invite
```

Send them the address plus their code, and they run `/joinserver <address>
<code>`. After that they just use `/server <name>` — their membership is
saved.

### Manage it

```sh
systemctl status tor2-server            # running?      (--user if installed as a user)
journalctl -u tor2-server -f            # live logs
tor2-server ~/.local/share/tor2-server --address   # what's my address?
tor2-server ~/.local/share/tor2-server --info      # address, channels, members
tor2-server ~/.local/share/tor2-server --backup ~/backups   # save everything
```

**Back it up.** The backup archive holds the database, stored media *and the
onion identity key* — without it, a dead machine means a new address and
everyone re-inviting. Restore by untarring it into an empty data directory.
Treat the file as securely as the server itself.

Admin commands inside tor2: `/mkchan`, `/rmchan`, `/kick`, `/ban`, `/unban`,
`/bans`, `/promote`, `/demote`, `/newinvite`, `/autoupdate`.

### Auto-updating (optional, off by default)

An admin can let the server update itself from GitHub:

```
/autoupdate on      check daily and restart onto new versions
/autoupdate now     check once, right now
/autoupdate off     stop (the default)
```

It only ever **fast-forwards** the checkout, refuses if you have local
changes or a different git remote, and never merges or resets. Even so, this
runs code downloaded from the internet — only enable it if you trust that
repository completely.

---

## 4. Command reference

### Anywhere

| command | what it does |
|---|---|
| `/help` | list commands for where you are |
| **ctrl+n** / **ctrl+p** | switch between open conversations (or click one) |
| `/nick <name>` | set your display name (remembered) |
| `/settings` | notifications, theme, previews, quality, transfer streams |
| **tab** | complete a command, channel or nickname |
| `/update` | get the latest version from GitHub |
| `/disconnect` | leave the current chat or server |
| `ctrl+q` | quit |

### One-to-one chat

| command | what it does |
|---|---|
| `/code` · `/join <code>` | pair with a 5-digit code |
| `/connect <name-or-address>` | connect to a saved contact or address |
| `/accept` · `/reject` | answer an incoming request |
| `/add <name>` | save the person you're talking to |
| `/contacts` · `/delcontact <name>` | list / remove contacts |

### On a server

| command | what it does |
|---|---|
| **click a channel** or **↑ / ↓** | switch channels (typing still works) |
| `/joinserver <address> <invite>` | join for the first time |
| `/server [name]` | reconnect a saved server, or list them |
| `/ch <name>` · `/channels` · `/members` | switch / list channels, see who's on |
| `/more` | load older messages |
| `/del <id>` | delete your message (admins: anyone's) |
| `/leave` | disconnect and forget this server |

### Pictures and video

| command | what it does |
|---|---|
| `/img <path>` | send an image — it previews for everyone automatically |
| `/vid <path>` | send a video, auto-compressed (up to 10 min) |
| `/big-vid <path>` | send a long video — any length, up to 3 GB |
| `/get <id>` | download something posted in a channel |
| `/preview <id>` | see a still of a video without downloading it |
| `/save [id]` | keep a previewed image (they aren't saved unless you ask) |
| `/audio <path>` | send music or a voice recording (converted to mp3) |
| `/play [id\|file]` | replay an animated GIF, or play audio out loud |

Videos are previewed from a still image the sender attaches, so you can see
what something is before downloading it. Compression and transfers show a
progress bar.

Downloads land in `./received/`. Animated GIFs play in a pane above the input
bar.

### Admin only

| command | what it does |
|---|---|
| `/newinvite [uses] [admin]` | mint an invite code |
| `/mkchan <name>` · `/rmchan <name>` | create / delete a channel |
| `/kick <nick>` | remove a member (they can rejoin with a new invite) |
| `/ban <nick> [reason]` · `/unban <nick>` · `/bans` | block a name entirely |
| `/promote <nick>` · `/demote <nick>` | grant or remove admin |
| `/autoupdate on\|off\|now` | self-updating from GitHub |

---

## 5. How it keeps things private

**One-to-one chats are end-to-end encrypted** and involve no server at all.
Three layers protect every message:

1. **Tor's onion encryption.** An onion address is derived from a public key,
   so reaching the right address proves you reached the right machine. It also
   hides your IP from the other person.
2. **A per-session key exchange** (X25519). New keys every session, so
   recording today's traffic doesn't help anyone who steals a key tomorrow.
   This produces the **fingerprint** you read aloud to catch an impostor.
3. **A tor2-only inner cipher.** Even something holding the outer key can't
   read or forge tor2 frames without implementing this layer.

**On a server, the operator can read channel messages.** The daemon has to
decrypt them to deliver and store them — the same as self-hosted Matrix or
Mattermost. Run it yourself, and use one-to-one chats for anything the host
shouldn't see.

Other protections:

- **Nobody talks to you unless you accept.** Knowing your address or code only
  lets someone ask.
- **Servers need an invite.** Secrets are stored only as hashes, so a stolen
  server database can't be used to log in.
- **No command surface.** The protocol carries chat and media, nothing that
  can run code or read your files. Anything unrecognized ends the session.
- **Media is verified** before it's kept: images must fully decode, videos
  must probe as real video, and everything must match its SHA-256.
- **Servers won't fill their own disk** — uploads are refused at 80% usage.
- **Encrypted at rest**: a server encrypts message bodies and media on disk
  with XSalsa20-Poly1305, so a stolen disk image or backup yields ciphertext.
  By default the key is a `0600` file in the data directory, which protects a
  stolen *database* or *backup* but not a copy of the whole directory. For
  stronger protection, run with `--passphrase` (or `TOR2_PASSPHRASE`) and the
  key is derived with Argon2id and never written down — at the cost of the
  server not being able to restart unattended.
- **Guessing is throttled**: ten failed invites or tokens within five minutes
  and the server stops accepting new attempts for a while.
- **Flood protection**: 15 messages per 10 seconds per member, so nobody can
  push a channel's history out through the retention limit.

### Speed

Transfers use two tricks to make Tor bearable, without weakening anything:

- media travels as **raw binary frames** rather than base64 inside JSON,
  which is 25% fewer bytes and far less CPU per chunk;
- large files are **split across several Tor circuits at once** (Tor gives a
  connection its own circuit when its SOCKS credentials differ), so a
  transfer is not capped by one circuit's bandwidth;
- circuits **take work from a shared queue** rather than being handed equal
  shares, so one slow relay costs a single piece instead of holding up the
  whole transfer while faster circuits idle;
- large pictures are **re-encoded to WebP** before sending — typically 80%
  smaller with no visible loss — and `/settings` offers **H.265** video,
  measured about 22% smaller than H.264 at matching quality (slower to
  encode, needs a recent player).

Set the number of streams in `/settings`; anything under 4 MB, or any
failure, falls back to a single circuit automatically.

**Limitations, honestly:** a live 5-digit code is guessable in principle
(100,000 options), though a guesser still only reaches your accept prompt.
Tor is slow, so large videos take a long time. And this is a hobby project,
not audited software — don't rely on it where your safety depends on it.

---

## 6. Troubleshooting

**"peer is not speaking the tor2 protocol (or runs an old version)"**
You and the other person are on different versions. Both run `/update` (or
`git pull`), then restart.

**Stuck on "bootstrapping tor"**
The first connection to the Tor network can take a couple of minutes on a slow
link. If it never finishes, check that `tor` is installed: `tor --version`.

**"invalid or used-up invite code"**
Invite codes work once. Ask the server admin for a fresh one with
`/newinvite`.

**The installer says it can't create a virtualenv**
On Debian and Ubuntu the venv module ships separately:
`apt install -y python3-venv`.

**Video is rejected as "not a readable video"**
ffmpeg isn't installed. Text and images work without it;
`apt install ffmpeg` (or your package manager's equivalent) enables video.

**A conversation dropped**
It reconnects by itself with backoff, using your saved membership. `/server
<name>` forces an immediate retry.

**A server command says "admin only"**
Only admins can manage channels and members. An existing admin can promote
you with `/promote <your-nick>`.

---

## 7. Development

```sh
.venv/bin/pip install -e . pytest pytest-timeout
.venv/bin/pytest tests -q
```

The suite covers the protocol, server flows, admin controls, multi-session
routing, encryption at rest, parallel transfers and the UI. It never touches
the network or your real config. See [CONTRIBUTING.md](CONTRIBUTING.md).
