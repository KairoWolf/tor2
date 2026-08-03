"""Client-side server mode: joining a tor2 server and using its channels.

Mixed into :class:`~tor2.app.Tor2App`. Kept separate from the direct-message
logic so the two flows stay easy to read independently.
"""

import asyncio
import base64
import hashlib
import re
import tempfile
import time
from pathlib import Path

from rich.text import Text
from textual.widgets import Static

from . import media, proto, settings, store, video
from .imgview import render_preview, validate_image
from .tornet import normalize_onion

# imported lazily from app to avoid a circular import at module load
from .app_consts import RECEIVED_DIR, fmt_duration, fmt_size
from .conv import current as current_conv

SERVER = "server"
HANDSHAKE_TIMEOUT = 60


class ChannelItem(Static):
    """One clickable channel row in the sidebar, with an unread badge."""

    def __init__(self, channel: str):
        super().__init__(classes="chan")
        self.channel = channel
        self.render_row(0, False)

    def render_row(self, unread: int, mentioned: bool) -> None:
        self.unread, self.mentioned = unread, mentioned
        t = Text()
        t.append(f"# {self.channel[:14]}")
        if mentioned:
            t.append("  @", style="bold red")
        elif unread:
            t.append(f"  {unread if unread < 100 else '99+'}", style="bold yellow")
        self.row_text = t.plain
        self.update(t)

    def on_click(self) -> None:
        self.app.run_worker(self.app.server_switch(self.channel), exclusive=False)


class ServerModeMixin:
    """Requires the host class to provide: session, state, sys_msg, chat,
    set_status, tor, nick, _require_connected, _next_received."""

    # ---------- joining ----------

    async def join_server(self, arg: str) -> None:
        """/joinserver <onion> <invite> [local-name]"""
        parts = arg.split()
        if len(parts) < 2:
            self.sys_msg("usage: /joinserver <onion> <invite-code> [name]", "red")
            return
        onion, invite = parts[0], parts[1]
        local = parts[2] if len(parts) > 2 else ""
        try:
            onion = normalize_onion(onion)
        except ValueError as e:
            self.sys_msg(str(e), "red")
            return
        await self._connect_server(onion, invite=invite, local_name=local)

    async def open_server(self, name: str) -> None:
        """/server <saved-name> — reconnect using the stored token."""
        servers = store.load_servers()
        if not name:
            if not servers:
                self.sys_msg("no saved servers — /joinserver <onion> <invite>")
                return
            for n, s in sorted(servers.items()):
                t = Text("  ")
                t.append(f"{n:<20}", style="bold cyan")
                t.append(s.get("onion", "")[:24] + "…", style="bright_black")
                self.chat.write(t)
            return
        entry = servers.get(name)
        if not entry:
            self.sys_msg(f"no saved server “{name}” — /server lists them", "red")
            return
        await self._connect_server(entry["onion"], token=entry.get("token", ""),
                                   local_name=name)

    async def _connect_server(self, onion: str, invite: str = "", token: str = "",
                              local_name: str = "", quiet: bool = False) -> None:
        existing = self.convs.get(f"srv:{onion}")
        if existing is not None and existing.session is not None:
            self.sys_msg("already connected to that server", "red")
            self.select_conv(existing)
            return
        if not quiet:
            self.sys_msg(f"connecting to server {onion[:16]}… (can take ~15s)")
        try:
            reader, writer = await self.tor.dial(onion)
            session = await proto.handshake(reader, writer)
        except Exception as e:
            if not quiet:
                self.sys_msg(f"connection failed: {e}", "red")
            return
        try:
            hello = await asyncio.wait_for(session.recv(), timeout=HANDSHAKE_TIMEOUT)
        except Exception as e:
            await session.close()
            if not quiet:
                self.sys_msg(f"no response from server: {e}", "red")
            return
        if hello.get("t") != "srvhello":
            await session.close()
            self.sys_msg("that address is a person, not a server — use /connect", "red")
            return

        name = str(hello.get("name", "server"))[:40]
        conv = self.add_conv(f"srv:{onion}", "server", local_name or name, session)
        conv.manual_disconnect = False
        tok = current_conv.set(conv)
        try:
            self.state = SERVER
            self.srv = {
                "onion": onion, "name": name,
                "local": local_name, "channel": "", "channels": [], "admin": False,
                "online": [], "buffers": {}, "download": None,
            }
            await session.send({"t": "auth", "nick": self.nick,
                                "invite": invite, "token": token})
        finally:
            current_conv.reset(tok)
        self.run_worker(self.server_loop(session, conv), group="net",
                        exclusive=False)

    async def server_loop(self, session: proto.Session, conv=None) -> None:
        current_conv.set(conv)          # this task's conversation, for handlers
        self.touch_rx()
        self.run_worker(self.keepalive(session, conv), group="net",
                        exclusive=False)
        lost = False
        try:
            while True:
                msg = await session.recv()
                self.touch_rx()
                if msg["t"] in ("ping", "pong"):
                    if msg["t"] == "ping":
                        await session.send({"t": "pong"})
                    continue
                await self.handle_server_msg(msg)
        except (asyncio.IncompleteReadError, ConnectionError):
            if self.session is session:
                self.sys_msg("connection to the server dropped", "red")
                lost = True
        except Exception as e:
            if self.session is session:
                self.sys_msg(f"server session error: {e}", "red")
                lost = True
        finally:
            if self.session is session:
                entry = {"onion": self.srv.get("onion"),
                         "local": self.srv.get("local")}
                manual = self.conv.manual_disconnect
                await self.drop_session()
                self.manual_disconnect = manual
                # Tor circuits die routinely; get back on by ourselves rather
                # than making the user retype /server.
                if lost and entry["onion"] and not self.manual_disconnect:
                    self.run_worker(self.reconnect_loop(entry), group="net",
                                    exclusive=False)

    async def reconnect_loop(self, entry: dict) -> None:
        """Retry a dropped server session with backoff until it works."""
        delays = [5, 10, 20, 40, 60, 120]
        for attempt, delay in enumerate(delays, 1):
            await asyncio.sleep(delay)
            if self.session is not None or self.manual_disconnect:
                return
            saved = store.load_servers().get(entry["local"] or "", {})
            token = saved.get("token", "")
            if not token:
                self.sys_msg("cannot reconnect automatically without a saved "
                             "membership — use /joinserver", "red")
                return
            self.sys_msg(f"reconnecting… (attempt {attempt} of {len(delays)})")
            await self._connect_server(entry["onion"], token=token,
                                       local_name=entry["local"] or "",
                                       quiet=True)
            if self.session is not None:
                return
        self.sys_msg("could not reconnect — use /server to try again", "red")

    # ---------- incoming ----------

    async def handle_server_msg(self, msg: dict) -> None:
        kind = msg["t"]
        if kind == "authok":
            self.srv_authok(msg)
        elif kind == "event":
            self.srv_event(msg)
        elif kind == "histbatch":
            self.srv_histbatch(msg)
        elif kind == "members":
            self.srv["channels"] = [str(c) for c in msg.get("channels", [])]
            self.srv["online"] = [str(n) for n in msg.get("online", [])]
            self.refresh_sidebar()
        elif kind == "srverr":
            self.sys_msg(str(msg.get("msg", ""))[:400], "yellow")
        elif kind == "deleted":
            self.srv_deleted(msg)
        elif kind == "mget":
            self.srv_mget(msg)
        elif kind == "mgchunk":
            await self.srv_mgchunk(msg)

    def srv_authok(self, msg: dict) -> None:
        s = self.srv
        s["name"] = str(msg.get("server", s["name"]))[:40]
        s["admin"] = bool(msg.get("admin"))
        s["channels"] = [str(c) for c in msg.get("channels", [])]
        s["channel"] = str(msg.get("channel", "general"))
        server_nick = str(msg.get("nick", self.nick))[:32]
        token = msg.get("token")

        local = s["local"] or "".join(
            ch for ch in s["name"].lower().replace(" ", "-") if ch.isalnum() or ch in "-_"
        )[:32] or "server"
        s["local"] = local
        try:
            store.save_server(local, s["onion"], token or "", s["name"])
        except ValueError:
            pass

        self.sys_msg(f"joined “{s['name']}” as {server_nick}"
                     + (" (admin)" if s["admin"] else ""), "green")
        if token:
            self.sys_msg(f"membership saved — reconnect later with /server {local}",
                         "green")
        self.sys_msg("note: a server operator can read channel messages "
                     "(DMs stay end-to-end)", "bright_black")
        self.conv.title = local or s["name"]
        self.refresh_convs()
        if self.conv is self.active:
            self.show_sidebar(True)
            self.refresh_sidebar()
            self.update_server_status()

    def srv_histbatch(self, msg: dict) -> None:
        chan = str(msg.get("chan", ""))
        buf = self.srv["buffers"].setdefault(chan, [])
        msgs = msg.get("msgs", [])
        if msg.get("append"):          # an older page from /more
            if not msgs:
                self.sys_msg("no older messages", "bright_black")
                return
            self.srv.setdefault("oldest", {})[chan] = msgs[0].get("id")
            older = []
            for m in msgs:
                hit = (str(m.get("nick")) != self.nick
                       and self.mentions_me(str(m.get("body") or "")))
                older.append(self.render_event(m, historic=True, mentioned=hit))
            buf[:0] = older
            if chan == self.srv["channel"]:
                self.redraw_channel()
                self.sys_msg(f"loaded {len(older)} older messages", "bright_black")
            return
        buf.clear()
        if msgs:
            self.srv.setdefault("oldest", {})[chan] = msgs[0].get("id")
        for m in msgs[-200:]:
            hit = (str(m.get("nick")) != self.nick
                   and self.mentions_me(str(m.get("body") or "")))
            buf.append(self.render_event(m, historic=True, mentioned=hit))
        if chan == self.srv["channel"]:
            self.redraw_channel()

    def srv_event(self, msg: dict) -> None:
        chan = str(msg.get("chan", ""))
        mine = str(msg.get("nick")) == self.nick
        if mine and self.drop_pending(chan, msg):
            return                     # already shown by local echo
        mentioned = (not mine) and self.mentions_me(str(msg.get("body") or ""))
        line = self.render_event(msg, mentioned=mentioned)
        self.srv["buffers"].setdefault(chan, []).append(line)
        if mentioned:
            self.notify_user(f"{msg.get('nick')} mentioned you in #{chan}", True)
            if chan != self.srv["channel"]:
                self.sys_msg(f"{msg.get('nick')} mentioned you in #{chan}", "red")
        if chan == self.srv["channel"]:
            self.chat.write(line)
            inline = msg.get("inline")
            if inline:
                self.render_inline_image(msg, inline)
            else:
                self.render_thumb(msg.get("media"))
        elif not mine:
            counts = self.srv.setdefault("unread", {})
            counts[chan] = counts.get(chan, 0) + 1
            if mentioned:
                self.srv.setdefault("mentions", set()).add(chan)
            if self.conv is self.active:
                self.mark_active_channel()
        if not mine:
            self.bump_unread(self.conv, mentioned)
            if not mentioned and self.conv is not self.active:
                self.notify_user(f"new message in #{chan}")

    def srv_deleted(self, msg: dict) -> None:
        chan = str(msg.get("chan", ""))
        self.sys_msg(f"message {msg.get('id')} deleted by {msg.get('by')} "
                     f"in #{chan}", "bright_black")
        # cheapest correct refresh: ask the server for the channel again
        if chan == self.srv.get("channel") and self.session is not None:
            self.run_worker(self.session.send({"t": "history", "chan": chan}),
                            exclusive=False)

    def drop_pending(self, chan: str, msg: dict) -> bool:
        """Reconcile the server's echo with the copy we drew locally.

        Returns True if this message was already on screen, in which case the
        local line is upgraded in place with the id the server assigned.
        """
        pend = self.srv.get("pending") or []
        body = str(msg.get("body", ""))
        for i, (pchan, pbody, line) in enumerate(pend):
            if pchan == chan and pbody == body:
                pend.pop(i)
                mid = msg.get("id")
                if mid is not None and not line.plain.startswith(f"{line.plain[:5]}[{mid}]"):
                    line.append(f"  [{mid}]", style="bright_black")
                    if chan == self.srv.get("channel") and self.conv is self.active:
                        self.redraw_channel()
                return True
        return False

    def mentions_me(self, body: str) -> bool:
        """@nick, or the bare nick as a whole word."""
        if not body:
            return False
        nick = re.escape(self.nick)
        return bool(re.search(rf"(?<![\w@])@?{nick}\b", body, re.IGNORECASE))

    def render_thumb(self, info: dict | None) -> None:
        """Preview a video from the small thumbnail its sender attached, so
        members see what it is before spending minutes downloading it."""
        if not info or info.get("kind") != "vid" or not info.get("thumb"):
            return
        try:
            data = base64.b64decode(info["thumb"], validate=True)
            validate_image(data)
        except Exception:
            return
        try:
            self.chat.write(render_preview(data, width=44))
        except Exception:
            pass

    def render_event(self, m: dict, historic: bool = False,
                     mentioned: bool = False) -> Text:
        ts = time.strftime("%H:%M", time.localtime(m.get("ts", time.time())))
        nick = str(m.get("nick", "?"))[:32]
        t = Text()
        t.append(f"{ts} ", style="bright_black")
        if m.get("id") is not None:
            t.append(f"[{m['id']}] ", style="bright_black")
        t.append(f"{nick} ", style="bold cyan" if nick == self.nick else "bold magenta")
        t.append("▸ ", style="bright_black")
        info = m.get("media")
        if info:
            kind = {"img": "image", "aud": "audio"}.get(info.get("kind"), "video")
            t.append(f"[{kind} · {fmt_size(info.get('size', 0))}] ", style="magenta")
            t.append(f"/get {info.get('id')}", style="bold cyan")
        else:
            body = str(m.get("body", ""))
            if mentioned:
                t.append(body, style="bold on #4a3000")
            else:
                t.append(body)
        return t

    def render_inline_image(self, msg: dict, inline: str) -> None:
        """Preview a pushed image. It is *not* written to disk — /get or
        /save does that, so the channel doesn't litter your filesystem."""
        try:
            data = base64.b64decode(inline, validate=True)
            if len(data) > proto.MAX_IMAGE_BYTES:
                raise ValueError("too large")
            fmt = validate_image(data)
        except Exception as e:
            self.sys_msg(f"could not display image: {e}", "red")
            return
        mid = (msg.get("media") or {}).get("id")
        if mid is not None:
            self.cache.put(str(mid), data, fmt)
        self.show_preview(data, f"/save {mid} keeps it, /get {mid} downloads it"
                          if mid is not None else "/save keeps it")

    # ---------- media download ----------

    def srv_mget(self, msg: dict) -> None:
        try:
            size = int(msg.get("size", 0))
            chunks = int(msg.get("chunks", 0))
            if not (0 < size <= proto.MAX_BIG_VIDEO_BYTES):
                raise ValueError("bad size")
            if not media.plausible_chunk_count(size, chunks):
                raise ValueError("bad chunk count")
            RECEIVED_DIR.mkdir(exist_ok=True)
            if not media.room_for(RECEIVED_DIR, size):
                raise ValueError(f"not enough free disk space for {fmt_size(size)}")
        except (TypeError, ValueError) as e:
            self.sys_msg(f"rejected download: {e}", "red")
            return
        ext = "".join(ch for ch in str(msg.get("ext", "mp4"))[:5] if ch.isalnum()) or "mp4"
        old = self.srv.get("download")
        if old:
            old["sink"].abort()
        sink = media.ChunkSink(size, chunks, str(msg.get("sha256", ""))[:64],
                               ext=ext, tmp_dir=RECEIVED_DIR)
        self.srv["download"] = {"sink": sink, "kind": str(msg.get("kind", "vid")),
                                "ext": ext, "id": msg.get("id")}
        self.sys_msg(f"downloading {fmt_size(size)}…", "magenta")
        self.start_transfer("downloading", size)
        mid = msg.get("id")
        if mid is not None:
            self.run_worker(self.try_parallel(int(mid), dict(msg)),
                            group="net", exclusive=False)

    async def srv_mgchunk(self, msg: dict) -> None:
        dl = self.srv.get("download")
        if dl is None:
            return
        sink = dl["sink"]
        try:
            data = msg.get("bin")
            if data is None:
                data = base64.b64decode(msg.get("data", ""), validate=True)
            off = msg.get("off")
            sink.write(data, offset=int(off) if off is not None else None)
        except Exception as e:
            sink.abort()
            self.srv["download"] = None
            self.sys_msg(f"download failed: {e}", "red")
            self.update_server_status()
            return
        self.transfer_progress(sink.got)
        if not sink.complete:
            return
        await self.finish_download()

    async def finish_download(self) -> None:
        dl = self.srv.get("download")
        if dl is None:
            return
        sink = dl["sink"]
        if not sink.complete:
            return
        self.srv["download"] = None
        self.progress_done()
        prefix = {"img": "img", "aud": "aud"}.get(dl["kind"], "vid")
        dest = self._next_received(prefix, dl["ext"])
        try:
            await asyncio.to_thread(sink.finish, dest)
        except (ValueError, OSError) as e:
            self.sys_msg(f"download failed: {e}", "red")
            self.update_server_status()
            return
        if dl["kind"] == "vid" and video.have_ffmpeg():
            try:
                await asyncio.to_thread(video.validate_received, dest)
            except Exception as e:
                dest.unlink(missing_ok=True)
                self.sys_msg(f"rejected download: {e}", "red")
                self.update_server_status()
                return
        self.sys_msg(f"downloaded → {dest}", "green")
        if dl["kind"] == "img":       # show what just arrived
            try:
                data = dest.read_bytes()
                self.cache.put(str(dl.get("id", dest.stem)), data, dl["ext"])
                self.show_preview(data)
            except Exception:
                pass
        elif dl["kind"] == "vid":
            await self.preview_video(dest)
        self.update_server_status()

    # ---------- outgoing ----------

    def _srv_ready(self) -> bool:
        """The session can vanish between keystrokes; never send into a None."""
        if self.state != SERVER or self.session is None or not self.srv.get("channel"):
            self.sys_msg("not connected to a server", "red")
            return False
        return True

    async def server_post(self, body: str) -> None:
        if not self._srv_ready():
            return
        chan = self.srv["channel"]
        # Draw it straight away: waiting for the server's echo over Tor makes
        # your own typing feel a second slow.
        pending = self.render_event({"nick": self.nick, "ts": time.time(),
                                     "body": body, "id": None})
        self.srv["buffers"].setdefault(chan, []).append(pending)
        self.srv.setdefault("pending", []).append((chan, body, pending))
        if chan == self.srv.get("channel") and self.conv is self.active:
            self.chat.write(pending)
        await self.session.send({"t": "post", "chan": chan, "body": body})

    async def server_switch(self, chan: str) -> None:
        if not self._srv_ready():
            return
        chan = chan.strip().lstrip("#").lower()
        if not chan:
            self.sys_msg("usage: /ch <channel>", "red")
            return
        if chan not in self.srv["channels"]:
            self.sys_msg(f"no channel #{chan} (see the sidebar)", "red")
            return
        if chan == self.srv.get("channel"):
            return
        self.srv["channel"] = chan
        self.srv.setdefault("unread", {}).pop(chan, None)
        self.srv.setdefault("mentions", set()).discard(chan)
        await self.session.send({"t": "switch", "chan": chan})
        self.redraw_channel()
        self.mark_active_channel()
        self.update_server_status()

    async def server_send_media(self, path_str: str, kind: str,
                                big: bool = False) -> None:
        if not self._srv_ready():
            return
        path = Path(path_str).expanduser()
        if not path.is_file():
            self.sys_msg(f"no such file: {path}", "red")
            return
        thumb = None
        if kind == "aud":
            prepared = await self.prepare_audio(path)
            if prepared is None:
                self.update_server_status()
                return
            payload, tmpdir = prepared
            ext = "mp3"
        elif kind == "img":
            blob = path.read_bytes()
            if len(blob) > proto.MAX_IMAGE_BYTES:
                self.sys_msg("image too large (5 MB max)", "red")
                return
            try:
                ext = validate_image(blob)
            except Exception as e:
                self.sys_msg(f"not a supported image: {e}", "red")
                return
            payload, tmpdir = path, None
        else:
            if not video.have_ffmpeg():
                self.sys_msg("ffmpeg is required to send video", "red")
                return
            prepared = await self.prepare_video(path, big)
            if prepared is None:
                self.update_server_status()
                return
            payload, tmpdir = prepared
            ext = "mp4"
            thumb = await asyncio.to_thread(video.thumbnail, payload)

        try:
            if self.state != SERVER or self.session is None:
                return
            size = payload.stat().st_size
            if kind == "vid":
                self.sys_msg(f"uploading {fmt_size(size)} "
                             "(tor is slow — this can take a while)")
            self.start_transfer("uploading", size)
            self.progress("uploading", 0.0, "checksumming…")
            sha = await asyncio.to_thread(media.sha256_file, payload)
            meta = {"t": "mput", "kind": kind, "ext": ext,
                    "chan": self.srv["channel"],
                    "thumb": base64.b64encode(thumb).decode() if thumb else None}
            if not await self.parallel_upload(payload, meta, sha, size):
                await media.send_file(
                    self.session, payload, meta, "mchunk",
                    proto.chunk_size_for(size), sha,
                    on_progress=lambda sent, total: self.transfer_progress(sent),
                    keep_going=lambda: self.state == SERVER)
        except ConnectionError:
            self.sys_msg("upload aborted: session ended", "red")
            return
        finally:
            self.progress_done()
            if tmpdir is not None:
                tmpdir.cleanup()
            self.update_server_status()
        self.sys_msg("uploaded ✓ — the server will confirm or reject it", "green")

    async def server_more(self) -> None:
        """/more — page further back into stored history."""
        if not self._srv_ready():
            return
        chan = self.srv["channel"]
        oldest = self.srv.get("oldest", {}).get(chan)
        if not oldest:
            self.sys_msg("nothing loaded yet", "red")
            return
        await self.session.send({"t": "history", "chan": chan, "before": oldest})

    async def server_delete(self, arg: str) -> None:
        """/del <id> — remove a message (yours, or anyone's if admin)."""
        if not self._srv_ready():
            return
        try:
            mid = int(arg.strip())
        except ValueError:
            self.sys_msg("usage: /del <message id>", "red")
            return
        await self.session.send({"t": "del", "id": mid})

    def stream_count(self) -> int:
        try:
            n = int(settings.get("parallel_streams"))
        except (TypeError, ValueError):
            n = 4
        return max(1, min(8, n))

    async def extra_circuits(self, n: int) -> list:
        """Open n more sessions to this server, each on its own Tor circuit.

        Tor isolates circuits by SOCKS credentials, so these genuinely run in
        parallel rather than sharing one circuit's bandwidth.
        """
        onion = self.srv.get("onion")
        token = store.load_servers().get(self.srv.get("local") or "", {}).get("token")
        if not onion or not token:
            return []

        async def one(i: int):
            try:
                reader, writer = await self.tor.dial(onion, stream=f"tor2-x{i}")
                sess = await proto.handshake(reader, writer)
                hello = await asyncio.wait_for(sess.recv(), timeout=HANDSHAKE_TIMEOUT)
                if hello.get("t") != "srvhello":
                    raise ConnectionError("not a server")
                await sess.send({"t": "auth", "nick": self.nick, "token": token})
                ok = await asyncio.wait_for(sess.recv(), timeout=HANDSHAKE_TIMEOUT)
                if ok.get("t") != "authok":
                    raise ConnectionError("auth refused on extra circuit")
                return sess
            except Exception:
                return None

        got = await asyncio.gather(*(one(i) for i in range(n)))
        return [g for g in got if g is not None]

    async def parallel_upload(self, payload, meta: dict, sha: str,
                              size: int) -> bool:
        """Push one file up several circuits at once. True if it was sent."""
        streams = self.stream_count()
        if streams < 2 or size < 4 * 1024 * 1024:
            return False
        sessions = await self.extra_circuits(streams - 1)
        if not sessions:
            return False
        self.sys_msg(f"uploading over {len(sessions) + 1} circuits…",
                     "bright_black")

        chunk = proto.chunk_size_for(size)
        n_chunks = max(1, (size + chunk - 1) // chunk)
        # the announcement must arrive first; extra circuits then feed the
        # same upload, which the server tracks per member rather than per
        # connection
        await self.session.send({**meta, "size": size, "chunks": n_chunks,
                                 "sha256": sha})
        n = len(sessions) + 1
        span = size // n
        bounds = [(i * span, size if i == n - 1 else (i + 1) * span)
                  for i in range(n)]
        done = 0
        lock = asyncio.Lock()

        async def push(sess, start, end):
            nonlocal done
            async def note(k: int) -> None:
                nonlocal done
                async with lock:
                    done += k
                    self.transfer_progress(done)
            await media.send_file(
                sess, payload, meta, "mchunk", chunk, sha,
                announce=False, start=start, end=end,
                sent_cb=lambda k: self.transfer_progress(min(size, done + k)),
                keep_going=lambda: self.state == SERVER)
            async with lock:
                done += end - start
                self.transfer_progress(done)

        try:
            await asyncio.gather(
                push(self.session, *bounds[0]),
                *(push(sess, a, b) for sess, (a, b) in zip(sessions, bounds[1:])))
        except Exception as e:
            self.sys_msg(f"parallel upload failed ({e}) — retrying on one circuit",
                         "yellow")
            return False
        finally:
            for sess in sessions:
                await sess.close()
        return True

    async def try_parallel(self, mid: int, hdr: dict) -> None:
        """Attempt a multi-circuit download; the normal single-circuit stream
        continues underneath if it does not work out."""
        try:
            if await self.parallel_download(mid, hdr):
                await self.finish_download()
        except Exception as e:
            self.sys_msg(f"parallel download error: {e}", "yellow")

    async def parallel_download(self, mid: int, hdr: dict) -> bool:
        """Fetch one file over several circuits at once. True if it worked."""
        streams = self.stream_count()
        size = int(hdr["size"])
        if streams < 2 or size < 4 * 1024 * 1024:
            return False
        sessions = await self.extra_circuits(streams - 1)
        if not sessions:
            return False
        self.sys_msg(f"downloading over {len(sessions) + 1} circuits…",
                     "bright_black")
        dl = self.srv.get("download")
        if dl is None:
            return False
        sink = dl["sink"]
        n = len(sessions) + 1
        span = size // n
        bounds = [(i * span, size if i == n - 1 else (i + 1) * span)
                  for i in range(n)]
        done = 0
        lock = asyncio.Lock()

        async def pull(sess, start, end):
            nonlocal done
            await sess.send({"t": "fetch", "id": mid, "start": start, "end": end})
            while True:
                msg = await asyncio.wait_for(sess.recv(), timeout=300)
                if msg["t"] != "mgchunk":
                    continue
                data = msg.get("bin")
                if data is None:
                    continue
                async with lock:
                    sink.write(data, offset=int(msg.get("off", start)))
                    done += len(data)
                    self.transfer_progress(done)
                start += len(data)
                if start >= end:
                    return

        try:
            # the main session takes the first slice, the extra circuits the rest
            tasks = [asyncio.create_task(pull(self.session, *bounds[0]))]
            for sess, (a, b) in zip(sessions, bounds[1:]):
                tasks.append(asyncio.create_task(pull(sess, a, b)))
            await asyncio.gather(*tasks)
        except Exception as e:
            self.sys_msg(f"parallel download failed ({e}) — falling back", "yellow")
            return False
        finally:
            for sess in sessions:
                await sess.close()
        return True

    async def server_fetch(self, arg: str) -> None:
        if not self._srv_ready():
            return
        try:
            mid = int(arg.strip())
        except ValueError:
            self.sys_msg("usage: /get <id>  (the number shown on a video)", "red")
            return
        await self.session.send({"t": "fetch", "id": mid})

    # ---------- display ----------

    def show_sidebar(self, visible: bool) -> None:
        sidebar = self.query_one("#sidebar")
        sidebar.display = visible

    def refresh_sidebar(self) -> None:
        conv = self.conv
        if conv.kind != "server" or conv is not self.active:
            return
        self.show_channel_list(True)
        s = conv.srv
        self.query_one("#srvname", Static).update(s["name"][:20])

        listed = [w.channel for w in self.query(ChannelItem)]
        if listed != s["channels"]:
            # Own group: an exclusive worker cancels others in its group, and
            # the session loop must never be one of them.
            self.run_worker(self.rebuild_channel_items(), group="sidebar",
                            exclusive=True)
        else:
            self.mark_active_channel()

        online = Text("online\n", style="bold")
        for n in s["online"][:12]:
            online.append(f" {n[:18]}\n",
                          style="green" if n == self.nick else "bright_black")
        self.query_one("#onlinelist", Static).update(online)

    async def rebuild_channel_items(self) -> None:
        chanlist = self.query_one("#chanlist")
        await chanlist.remove_children()
        await chanlist.mount_all([ChannelItem(c) for c in self.srv["channels"]])
        self.mark_active_channel()

    def mark_active_channel(self) -> None:
        active = self.srv.get("channel")
        counts = self.srv.get("unread", {})
        mentions = self.srv.get("mentions", set())
        for w in self.query(ChannelItem):
            is_active = w.channel == active
            w.set_class(is_active, "-active")
            w.render_row(0 if is_active else counts.get(w.channel, 0),
                         (not is_active) and w.channel in mentions)

    def cycle_channel(self, delta: int) -> None:
        """Arrow-key channel switching, so the input bar keeps focus."""
        if self.state != SERVER:
            return
        chans = self.srv.get("channels") or []
        if len(chans) < 2:
            return
        here = self.srv.get("channel")
        i = chans.index(here) if here in chans else 0
        self.run_worker(self.server_switch(chans[(i + delta) % len(chans)]),
                        exclusive=False)

    def redraw_channel(self) -> None:
        self.chat.clear()
        chan = self.srv["channel"]
        for line in self.srv["buffers"].get(chan, []):
            self.chat.write(line)
        self.chat.write(Text(f"  — #{chan} —", style="bright_black"))

    def update_server_status(self, extra: str = "") -> None:
        if self.conv is not self.active:
            return
        s = self.srv
        right = f"#{s['channel']} · {len(s['online'])} online"
        if extra:
            right += f" · {extra}"
        self.status_line = f" {s['name']}  ·  {right}"
        self.query_one("#status").update(self.status_line)

    # ---------- admin ----------

    async def server_admin(self, cmd: str, arg: str) -> None:
        if not self._srv_ready():
            return
        if cmd == "/mkchan":
            await self.session.send({"t": "mkchan", "name": arg.strip().lstrip("#").lower()})
        elif cmd == "/rmchan":
            await self.session.send({"t": "rmchan", "name": arg.strip().lstrip("#").lower()})
        elif cmd == "/newinvite":
            parts = arg.split()
            uses = 1
            admin = "admin" in parts
            for p in parts:
                if p.isdigit():
                    uses = int(p)
            await self.session.send({"t": "newinvite", "uses": uses, "admin": admin})
        elif cmd == "/kick":
            if not arg.strip():
                self.sys_msg("usage: /kick <nick>", "red")
                return
            await self.session.send({"t": "kick", "nick": arg.strip()})
        elif cmd == "/ban":
            nick, _, reason = arg.strip().partition(" ")
            if not nick:
                self.sys_msg("usage: /ban <nick> [reason]", "red")
                return
            await self.session.send({"t": "ban", "nick": nick,
                                     "reason": reason.strip()})
        elif cmd == "/unban":
            if not arg.strip():
                self.sys_msg("usage: /unban <nick>", "red")
                return
            await self.session.send({"t": "unban", "nick": arg.strip()})
        elif cmd == "/bans":
            await self.session.send({"t": "bans"})
        elif cmd in ("/promote", "/demote"):
            if not arg.strip():
                self.sys_msg(f"usage: {cmd} <nick>", "red")
                return
            await self.session.send({"t": cmd.lstrip("/"), "nick": arg.strip()})
        elif cmd == "/autoupdate":
            await self.session.send({"t": "autoupdate", "mode": arg.strip()})
