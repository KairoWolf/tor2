# tor2

Encrypted two-person chat over Tor onion services, in your terminal.
Text and images only — no file transfer, no remote commands, no computer access.

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

## What it does

- On startup, tor2 launches its own private Tor process and publishes an
  **ephemeral v3 onion service** — it disappears when you quit, and nothing
  about the session is persisted. Your address is shown at the top of the
  screen.
- You and your peer swap onion addresses over any channel, and one of you
  runs `/connect <address>`. Reachability and NAT traversal are handled
  entirely by Tor — no port forwarding, no server in the middle.
- On top of Tor's own encryption, every session performs an ephemeral X25519
  key exchange (NaCl `Box`), giving forward secrecy and a **session
  fingerprint**. Read the fingerprint aloud to each other — if it matches on
  both ends, nobody is in the middle.

## Requirements

- Linux or macOS with **Tor installed**:
  - Arch: `sudo pacman -S tor`
  - Debian/Ubuntu: `sudo apt install tor`
  - macOS: `brew install tor`

  Tor does **not** need to be running as a service — tor2 starts and manages
  its own private instance.
- **Python 3.11 or newer**

## Install

```sh
git clone https://github.com/KairoWolf/tor2.git
cd tor2
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Use

Both people do the following:

1. **Start the app:**

   ```sh
   .venv/bin/python -m tor2
   ```

2. **Wait for bootstrap.** The status bar shows Tor's progress; the first run
   takes ~30–60 seconds. When it finishes, your own address appears:

   ```
   your address: ptlox4ic....onion   |   not connected
   ```

3. **Swap addresses.** Send your `.onion` address to your peer over any
   channel (it is public information — knowing it does not reveal your
   identity or location).

4. **One of you connects:**

   ```
   /connect theirlongonionaddress.onion
   ```

5. **Verify the fingerprint.** Both screens show the same short code like
   `6559-6030-521e-3711`. Read it to each other — matching codes mean the
   session is secure end-to-end.

6. **Chat.** Type to send text. Other commands:

   | command | effect |
   |---|---|
   | `/img <path>` | send an image (png/jpg/gif/webp/bmp, ≤5 MB) |
   | `/nick <name>` | set your display name |
   | `/disconnect` | drop the session |
   | `/help` | list commands |
   | `ctrl+q` | quit (also removes your onion service) |

Received images are validated, saved to `./received/`, and previewed inline
in the terminal.

## Safety properties

- Onion addresses are self-authenticating: connecting to the right address
  cryptographically guarantees you reached the holder of that key.
- Only three message types exist (`hello`, `txt`, `img`) — there is no file
  transfer, no remote command, and no way for a peer to access your computer.
  Unknown message types terminate the session.
- Sender-supplied filenames are ignored; received images are re-validated
  with PIL (decompression-bomb guard active) before being saved or rendered.
- Frames are hard-capped at 16 MB, images at 5 MB, nicknames sanitized.

## Limitations to understand

- Anyone who learns your onion address can attempt to connect while the app
  is running — the fingerprint check is what confirms *who* you're talking
  to. Share addresses carefully and always verify the fingerprint.
- This is a hobby project, not audited software. Do not rely on it in
  situations where your safety depends on it.
