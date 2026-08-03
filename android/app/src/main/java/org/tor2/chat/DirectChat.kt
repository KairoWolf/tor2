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
import java.net.Socket

/**
 * A one-to-one chat: no server involved, so nobody but the two people can read
 * it. Either side may open it, and the receiving side must accept before any
 * message is processed — knowing an address only lets someone ask.
 */
class DirectChat(
    private val tor: TorNet,
    private val scope: CoroutineScope,
    private val store: Store,
    private val myNick: String,
    val key: String,
    val onion: String,
    incoming: Session? = null,
) {
    val state = MutableStateFlow(
        if (incoming != null) ConnState.Incoming else ConnState.Idle)
    val peerNick = MutableStateFlow("peer")
    val fingerprint = MutableStateFlow(incoming?.fingerprint ?: "")
    val messages = MutableStateFlow<List<Message>>(emptyList())
    val notice = MutableStateFlow<String?>(null)
    val transfer = MutableStateFlow(TransferState())
    val unread = MutableStateFlow(0)

    private var session: Session? = incoming
    private var loop: Job? = null
    private var incomingFile: Incoming? = null

    private class Incoming(val size: Long, val sha: String, val file: File,
                           val handle: RandomAccessFile, var got: Long = 0,
                           val audio: Boolean = false)

    /** Start the receive loop for a connection someone else opened. */
    fun adoptIncoming(hello: JSONObject) {
        peerNick.value = hello.optString("nick", "peer").take(32)
        session?.let { s -> loop = scope.launch { receiveLoop(s) } }
    }

    /** Dial the other person and wait for them to accept. */
    suspend fun connect() {
        state.value = ConnState.Connecting
        try {
            val (socket, input, output) = tor.dial(onion)
            val s = handshake(input, output) { runCatching { socket.close() } }
            session = s
            fingerprint.value = s.fingerprint
            s.send(JSONObject().apply { put("t", "hello"); put("nick", myNick) })
            state.value = ConnState.WaitingToBeAccepted
            loop = scope.launch { receiveLoop(s) }
            scope.launch { keepalive(s) }
        } catch (e: Exception) {
            notice.value = e.message ?: "could not connect"
            state.value = ConnState.Failed
        }
    }

    /** Answer someone who asked to chat. */
    suspend fun accept() {
        val s = session ?: return
        s.send(JSONObject().apply { put("t", "accept"); put("nick", myNick) })
        state.value = ConnState.Connected
        scope.launch { keepalive(s) }
    }

    fun reject() {
        session?.close()
        session = null
        state.value = ConnState.Idle
    }

    private suspend fun keepalive(s: Session) {
        while (scope.isActive && session === s) {
            delay(Protocol.KEEPALIVE_SECONDS * 1000)
            if (session !== s) return
            runCatching { s.send(JSONObject().put("t", "ping")) }.onFailure { return }
        }
    }

    private suspend fun receiveLoop(s: Session) {
        try {
            while (scope.isActive && session === s) {
                val frame = s.receive()
                when (frame.type) {
                    "ping" -> s.send(JSONObject().put("t", "pong"))
                    "pong" -> {}
                    "hello" -> peerNick.value = frame.json.optString("nick", "peer")
                    "accept" -> {
                        peerNick.value = frame.json.optString("nick", "peer")
                        state.value = ConnState.Connected
                        notice.value = null
                    }
                    "txt" -> if (state.value == ConnState.Connected) {
                        add(Message(null, "dm", peerNick.value,
                                    System.currentTimeMillis(),
                                    frame.json.optString("body").take(10_000)))
                    }
                    "img" -> if (state.value == ConnState.Connected) onImage(frame.json)
                    "vmeta" -> if (state.value == ConnState.Connected) onMeta(frame.json)
                    "vchunk" -> frame.binary?.let { onChunk(frame.json, it) }
                }
            }
        } catch (e: Exception) {
            if (session === s) {
                notice.value = "the other end disconnected"
                state.value = ConnState.Idle
                session = null
            }
        }
    }

    private fun add(msg: Message) {
        messages.value = messages.value + msg
        if (!msg.mine) unread.value += 1
    }

    private fun onImage(j: JSONObject) {
        val data = runCatching {
            Base64.decode(j.optString("data"), Base64.DEFAULT)
        }.getOrNull() ?: return
        add(Message(null, "dm", peerNick.value, System.currentTimeMillis(), null,
                    inlineImage = data,
                    media = MediaInfo(-1, "img", j.optString("name", "image"),
                                      data.size.toLong(), "png")))
    }

    private fun onMeta(j: JSONObject) {
        val size = j.optLong("size")
        if (size <= 0 || size > 3L * 1024 * 1024 * 1024) return
        val audio = j.optBoolean("audio")
        val dir = File(store.mediaDir, "received").apply { mkdirs() }
        val file = File(dir, "dm_${System.currentTimeMillis()}." +
                (if (audio) "mp3" else "mp4"))
        runCatching {
            val raf = RandomAccessFile(file, "rw")
            raf.setLength(size)
            incomingFile = Incoming(size, j.optString("sha256"), file, raf, 0, audio)
            transfer.value = TransferState("receiving from ${peerNick.value}",
                                           0, size, true)
        }
    }

    private fun onChunk(j: JSONObject, data: ByteArray) {
        val inc = incomingFile ?: return
        runCatching {
            inc.handle.seek(j.optLong("off", inc.got))
            inc.handle.write(data)
            inc.got += data.size
            transfer.value = transfer.value.copy(done = inc.got)
            if (inc.got >= inc.size) {
                inc.handle.close()
                incomingFile = null
                transfer.value = TransferState()
                add(Message(null, "dm", peerNick.value, System.currentTimeMillis(),
                            null,
                            media = MediaInfo(-1, if (inc.audio) "aud" else "vid",
                                              inc.file.name, inc.size,
                                              if (inc.audio) "mp3" else "mp4")))
                notice.value = "saved ${inc.file.name}"
            }
        }
    }

    // ---------- sending ----------

    suspend fun send(text: String) {
        val s = session ?: return
        add(Message(null, "dm", myNick, System.currentTimeMillis(), text, mine = true))
        runCatching { s.send(JSONObject().apply { put("t", "txt"); put("body", text) }) }
            .onFailure { notice.value = "could not send" }
    }

    suspend fun sendImage(bytes: ByteArray, name: String) {
        val s = session ?: return
        if (bytes.size > Protocol.MAX_IMAGE) {
            notice.value = "image too large (5 MB max)"
            return
        }
        s.send(JSONObject().apply {
            put("t", "img"); put("name", name)
            put("data", Base64.encodeToString(bytes, Base64.NO_WRAP))
        })
        add(Message(null, "dm", myNick, System.currentTimeMillis(), null,
                    mine = true, inlineImage = bytes,
                    media = MediaInfo(-1, "img", name, bytes.size.toLong(), "png")))
    }

    suspend fun sendFile(file: File, audio: Boolean) = withContext(Dispatchers.IO) {
        val s = session ?: return@withContext
        val bytes = file.readBytes()
        val sha = Crypto.hex(Crypto.sha256(bytes))
        val chunk = Protocol.chunkSizeFor(bytes.size.toLong())
        transfer.value = TransferState("sending ${file.name}", 0,
                                       bytes.size.toLong(), true)
        s.send(JSONObject().apply {
            put("t", "vmeta"); put("size", bytes.size); put("sha256", sha)
            put("chunks", maxOf(1, (bytes.size + chunk - 1) / chunk))
            put("audio", audio); put("name", file.name)
        })
        var off = 0
        while (off < bytes.size) {
            val n = minOf(chunk, bytes.size - off)
            s.sendBinary(JSONObject().apply { put("t", "vchunk"); put("off", off) },
                         bytes, off, n)
            off += n
            transfer.value = transfer.value.copy(done = off.toLong())
        }
        transfer.value = TransferState()
        add(Message(null, "dm", myNick, System.currentTimeMillis(), null, mine = true,
                    media = MediaInfo(-1, if (audio) "aud" else "vid", file.name,
                                      bytes.size.toLong(),
                                      if (audio) "mp3" else "mp4")))
    }

    fun markRead() { unread.value = 0 }

    fun disconnect() {
        loop?.cancel()
        session?.close()
        session = null
        state.value = ConnState.Idle
    }
}
