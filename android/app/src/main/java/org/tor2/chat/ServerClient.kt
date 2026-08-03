package org.tor2.chat

import android.util.Base64
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.RandomAccessFile

/**
 * A live connection to a tor2 server: channels, history, media, admin.
 *
 * Mirrors the desktop client's behaviour, including the local echo that makes
 * your own messages appear immediately rather than after a round trip through
 * Tor, and reconnecting by itself when a circuit dies.
 */
class ServerClient(
    private val tor: TorNet,
    private val scope: CoroutineScope,
    private val store: Store,
    private val nick: String,
    val saved: SavedServer,
) {
    val state = MutableStateFlow(ConnState.Idle)
    val serverName = MutableStateFlow(saved.displayName)
    val channels = MutableStateFlow<List<Channel>>(emptyList())
    val online = MutableStateFlow<List<String>>(emptyList())
    val active = MutableStateFlow("general")
    val messages = MutableStateFlow<Map<String, List<Message>>>(emptyMap())
    val isAdmin = MutableStateFlow(false)
    val transfer = MutableStateFlow(TransferState())
    val notice = MutableStateFlow<String?>(null)
    val fingerprint = MutableStateFlow("")

    private var session: Session? = null
    private var loop: Job? = null
    private var reconnecting = false
    private var manualClose = false
    private val pending = mutableListOf<Pair<String, String>>()
    private var download: Download? = null
    private var permanentAddress: String? = null

    private class Download(
        val info: MediaInfo,
        val file: File,
        val handle: RandomAccessFile,
        var got: Long = 0,
    )

    suspend fun connect(invite: String = "", joinCode: String = "") {
        manualClose = false
        state.value = ConnState.Connecting
        try {
            val target = permanentAddress ?: saved.onion
            val (socket, input, output) = tor.dial(target)
            val s = handshake(input, output) { runCatching { socket.close() } }
            val hello = s.receive()
            if (hello.type != "srvhello") {
                s.close()
                notice.value = "that address is a person, not a server"
                state.value = ConnState.Failed
                return
            }
            serverName.value = hello.json.optString("name", saved.displayName)
            fingerprint.value = s.fingerprint
            session = s
            s.send(JSONObject().apply {
                put("t", "auth")
                put("nick", nick)
                put("invite", invite)
                put("code", joinCode)
                put("token", saved.token)
            })
            loop = scope.launch { receiveLoop(s) }
            scope.launch { keepalive(s) }
        } catch (e: Exception) {
            notice.value = e.message ?: "could not connect"
            state.value = ConnState.Failed
            scheduleReconnect()
        }
    }

    private suspend fun keepalive(s: Session) {
        while (scope.isActive && session === s) {
            delay(Protocol.KEEPALIVE_SECONDS * 1000)
            if (session !== s) return
            runCatching { s.send(JSONObject().put("t", "ping")) }
                .onFailure { return }
        }
    }

    private suspend fun receiveLoop(s: Session) {
        try {
            while (scope.isActive && session === s) {
                val frame = s.receive()
                when (frame.type) {
                    "ping" -> s.send(JSONObject().put("t", "pong"))
                    "pong" -> {}
                    else -> handle(frame)
                }
            }
        } catch (e: Exception) {
            if (session === s && !manualClose) {
                notice.value = "connection dropped — reconnecting"
                state.value = ConnState.Failed
                session = null
                scheduleReconnect()
            }
        }
    }

    /** Tor circuits die routinely; come back by ourselves rather than nagging. */
    private fun scheduleReconnect() {
        if (reconnecting || manualClose) return
        if (saved.token.isBlank() && permanentAddress == null) {
            notice.value = "This server was joined with a code that has since " +
                    "been used up. Ask for a new code."
            state.value = ConnState.Failed
            return
        }
        reconnecting = true
        scope.launch {
            val delays = listOf(5L, 10L, 20L, 40L, 60L, 120L)
            for ((i, wait) in delays.withIndex()) {
                delay(wait * 1000)
                if (manualClose || session != null) break
                notice.value = "Reconnecting… (${i + 1} of ${delays.size})"
                connect()
                if (session != null) break
            }
            reconnecting = false
            if (session == null && !manualClose) {
                notice.value = "Could not reconnect. Tap the server in the menu " +
                        "to try again."
                state.value = ConnState.Failed
            }
        }
    }

    private suspend fun handle(frame: Frame) {
        val j = frame.json
        when (frame.type) {
            "authok" -> {
                isAdmin.value = j.optBoolean("admin")
                val token = j.optString("token", "")
                // A join code's address is temporary — it stops working once
                // the code is spent — so save the permanent one the server
                // reports, or reconnecting later would reach nothing.
                val real = j.optString("address", "").ifBlank { saved.onion }
                permanentAddress = real
                if (token.isNotEmpty() || real != saved.onion) {
                    store.saveServer(saved.copy(
                        onion = real,
                        token = token.ifBlank { saved.token },
                        displayName = serverName.value))
                }
                val list = j.optJSONArray("channels")
                channels.value = (0 until (list?.length() ?: 0))
                    .map { Channel(list!!.getString(it)) }
                active.value = j.optString("channel", "general")
                state.value = ConnState.Connected
                notice.value = null
            }
            "members" -> {
                val chans = j.optJSONArray("channels")
                val names = (0 until (chans?.length() ?: 0)).map { chans!!.getString(it) }
                channels.value = names.map { name ->
                    channels.value.find { it.name == name } ?: Channel(name)
                }
                val on = j.optJSONArray("online")
                online.value = (0 until (on?.length() ?: 0)).map { on!!.getString(it) }
            }
            "histbatch" -> {
                val chan = j.optString("chan")
                val arr = j.optJSONArray("msgs") ?: return
                val loaded = (0 until arr.length()).map { toMessage(arr.getJSONObject(it), chan) }
                messages.value = messages.value.toMutableMap().apply {
                    this[chan] = if (j.optBoolean("append"))
                        loaded + (this[chan] ?: emptyList()) else loaded
                }
            }
            "event" -> onEvent(j)
            "deleted" -> {
                val id = j.optInt("id")
                messages.value = messages.value.mapValues { (_, list) ->
                    list.filterNot { it.id == id }
                }
                notice.value = "a message was deleted"
            }
            "srverr" -> notice.value = j.optString("msg")
            "mthumb" -> {
                val data = Base64.decode(j.optString("thumb"), Base64.DEFAULT)
                notice.value = "preview: ${j.optString("name").ifBlank { "#${j.optInt("id")}" }}"
                previewReady.value = data
            }
            "mget" -> startDownload(j)
            "mgchunk" -> frame.binary?.let { appendDownload(j, it) }
        }
    }

    val previewReady = MutableStateFlow<ByteArray?>(null)
    val downloadReady = MutableStateFlow<File?>(null)

    private fun onEvent(j: JSONObject) {
        val chan = j.optString("chan")
        val nickOf = j.optString("nick")
        val body = j.optString("body", "")
        val mine = nickOf == nick

        if (mine) {                      // reconcile with the local echo
            val idx = pending.indexOfFirst { it.first == chan && it.second == body }
            if (idx >= 0) {
                pending.removeAt(idx)
                messages.value = messages.value.toMutableMap().apply {
                    val list = (this[chan] ?: emptyList()).toMutableList()
                    val at = list.indexOfLast { it.pending && it.body == body }
                    if (at >= 0) list[at] = toMessage(j, chan) else list += toMessage(j, chan)
                    this[chan] = list
                }
                return
            }
        }
        val msg = toMessage(j, chan)
        messages.value = messages.value.toMutableMap().apply {
            this[chan] = (this[chan] ?: emptyList()) + msg
        }
        if (!mine && chan != active.value) {
            channels.value = channels.value.map {
                if (it.name == chan)
                    it.copy(unread = it.unread + 1, mentioned = it.mentioned || msg.mentioned)
                else it
            }
        }
        if (msg.mentioned) mentionAlert.value = "${msg.nick} mentioned you in #$chan"
    }

    val mentionAlert = MutableStateFlow<String?>(null)

    private fun toMessage(j: JSONObject, chan: String): Message {
        val body = if (j.isNull("body")) null else j.optString("body")
        val who = j.optString("nick")
        return Message(
            id = if (j.isNull("id")) null else j.optInt("id"),
            channel = chan,
            nick = who,
            timestamp = (j.optDouble("ts", 0.0) * 1000).toLong(),
            body = body,
            media = mediaFrom(j.optJSONObject("media")),
            mine = who == nick,
            mentioned = who != nick && mentionsMe(body),
            inlineImage = j.optString("inline", "").takeIf { it.isNotEmpty() }
                ?.let { Base64.decode(it, Base64.DEFAULT) },
        )
    }

    private fun mentionsMe(body: String?): Boolean {
        if (body.isNullOrBlank()) return false
        return Regex("(?<![\\w@])@?${Regex.escape(nick)}\\b", RegexOption.IGNORE_CASE)
            .containsMatchIn(body)
    }

    // ---------- actions ----------

    suspend fun post(text: String) {
        val s = session ?: return
        val chan = active.value
        // draw it immediately: waiting for the echo over Tor feels a second slow
        val echo = Message(null, chan, nick, System.currentTimeMillis(), text,
                           mine = true, pending = true)
        messages.value = messages.value.toMutableMap().apply {
            this[chan] = (this[chan] ?: emptyList()) + echo
        }
        pending += chan to text
        runCatching { s.send(JSONObject().apply {
            put("t", "post"); put("chan", chan); put("body", text)
        }) }.onFailure { notice.value = "could not send — reconnecting" }
    }

    suspend fun switchChannel(name: String) {
        active.value = name
        channels.value = channels.value.map {
            if (it.name == name) it.copy(unread = 0, mentioned = false) else it
        }
        session?.send(JSONObject().apply { put("t", "switch"); put("chan", name) })
    }

    suspend fun loadOlder() {
        val chan = active.value
        val oldest = messages.value[chan]?.firstOrNull { it.id != null }?.id ?: return
        session?.send(JSONObject().apply {
            put("t", "history"); put("chan", chan); put("before", oldest)
        })
    }

    suspend fun preview(id: Int) {
        session?.send(JSONObject().apply {
            put("t", "fetch"); put("id", id); put("thumb", true)
        })
    }

    suspend fun fetch(id: Int) {
        session?.send(JSONObject().apply { put("t", "fetch"); put("id", id) })
    }

    suspend fun admin(type: String, arg: String = "", extra: String = "") {
        session?.send(JSONObject().apply {
            put("t", type)
            if (arg.isNotEmpty()) put(if (type == "mkchan" || type == "rmchan") "name"
                                      else "nick", arg)
            if (type == "autoupdate") put("mode", arg)
            if (extra.isNotEmpty()) put("reason", extra)
        })
    }

    suspend fun deleteMessage(id: Int) {
        session?.send(JSONObject().apply { put("t", "del"); put("id", id) })
    }

    // ---------- media ----------

    suspend fun sendMedia(bytes: ByteArray, kind: String, ext: String, name: String,
                          thumb: ByteArray? = null) = withContext(Dispatchers.IO) {
        val s = session ?: return@withContext
        val sha = Crypto.hex(Crypto.sha256(bytes))
        val chunk = Protocol.chunkSizeFor(bytes.size.toLong())
        val chunks = maxOf(1, (bytes.size + chunk - 1) / chunk)
        transfer.value = TransferState("uploading ${name.ifBlank { kind }}",
                                       0, bytes.size.toLong(), true)
        s.send(JSONObject().apply {
            put("t", "mput"); put("kind", kind); put("ext", ext)
            put("chan", active.value); put("name", name)
            put("size", bytes.size); put("chunks", chunks); put("sha256", sha)
            if (thumb != null) put("thumb", Base64.encodeToString(thumb, Base64.NO_WRAP))
        })
        var off = 0
        while (off < bytes.size) {
            val n = minOf(chunk, bytes.size - off)
            s.sendBinary(JSONObject().apply { put("t", "mchunk"); put("off", off) },
                         bytes, off, n)
            off += n
            transfer.value = transfer.value.copy(done = off.toLong())
        }
        transfer.value = TransferState()
    }

    private fun startDownload(j: JSONObject) {
        val info = MediaInfo(j.optInt("id"), j.optString("kind"),
                             j.optString("name"), j.optLong("size"),
                             j.optString("ext", "bin"))
        val dir = File(store.mediaDir, "received").apply { mkdirs() }
        val safe = "media_${info.id}.${info.ext.filter { it.isLetterOrDigit() }}"
        val file = File(dir, safe)
        runCatching {
            val raf = RandomAccessFile(file, "rw")
            raf.setLength(info.size)
            download = Download(info, file, raf)
            transfer.value = TransferState("downloading ${info.display}", 0,
                                           info.size, true)
        }
    }

    private fun appendDownload(j: JSONObject, data: ByteArray) {
        val d = download ?: return
        runCatching {
            d.handle.seek(j.optLong("off", d.got))
            d.handle.write(data)
            d.got += data.size
            transfer.value = transfer.value.copy(done = d.got)
            if (d.got >= d.info.size) {
                d.handle.close()
                download = null
                transfer.value = TransferState()
                downloadReady.value = d.file
                notice.value = "saved ${d.info.display}"
            }
        }
    }

    fun disconnect() {
        manualClose = true
        loop?.cancel()
        session?.close()
        session = null
        state.value = ConnState.Idle
    }
}
