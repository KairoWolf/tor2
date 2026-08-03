# tor2

**Private chat in your terminal, over Tor.** Talk one-to-one, or run your own
server with Discord-style channels. No accounts, no phone numbers, no company
in the middle — just two computers finding each other through the Tor network.

```
 kairos-server  ·  #general · 3 online
┌──────────────┬────────────────────────────────────────────────┐
│ kairos       │ 14:02 mia   ▸ did the build finish?            │
│──────────────│ 14:02 kairo ▸ yeah, pushed it                  │
│ ▸ #general   │ 14:03 mia   ▸ [image · 240 KB] /get 7          │
│   #random    │                                                │
│   #music     │                                                │
│              │                                                │
│ online       │                                                │
│  kairo       │                                                │
│  mia         │                                                │
└──────────────┴────────────────────────────────────────────────┘
 message…  (/help for commands)
```

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
```

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
| `/nick <name>` | set your display name (remembered) |
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
| `/leave` | disconnect and forget this server |

### Pictures and video

| command | what it does |
|---|---|
| `/img <path>` | send an image — it previews for everyone automatically |
| `/vid <path>` | send a video, auto-compressed (up to 10 min) |
| `/big-vid <path>` | send a long video — any length, up to 3 GB |
| `/get <id>` | download something posted in a channel |
| `/save [id]` | keep a previewed image (they aren't saved unless you ask) |
| `/play [id]` | replay an animated GIF |

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

**A server command says "admin only"**
Only admins can manage channels and members. An existing admin can promote
you with `/promote <your-nick>`.
