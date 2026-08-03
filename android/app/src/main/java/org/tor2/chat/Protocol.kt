package org.tor2.chat

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.DataInputStream
import java.io.InputStream
import java.io.OutputStream

/**
 * The tor2 wire protocol, v4 — the same bytes the desktop client speaks.
 *
 * Three layers protect every message: Tor's own onion encryption underneath,
 * an ephemeral X25519 session layer, and a tor2-only inner SecretBox layer
 * whose key is derived from the session secret with a tor2 personalisation
 * string. Media travels as raw bytes behind a small JSON header rather than
 * base64, which is what keeps large transfers from being a third larger than
 * they need to be.
 */
object Protocol {
    val MAGIC = "TOR2".toByteArray(Charsets.ISO_8859_1)
    val INNER_MAGIC = "T2I1".toByteArray(Charsets.ISO_8859_1)
    val INNER_PERSON = "tor2-inner-v1".toByteArray(Charsets.ISO_8859_1)
    const val KIND_JSON = 'J'.code.toByte()
    const val KIND_BINARY = 'B'.code.toByte()

    const val MAX_FRAME = 16 * 1024 * 1024
    const val VIDEO_CHUNK = 512 * 1024
    const val BIG_CHUNK = 2 * 1024 * 1024
    const val MAX_VIDEO = 60 * 1024 * 1024
    const val MAX_IMAGE = 5 * 1024 * 1024
    const val KEEPALIVE_SECONDS = 25L

    fun chunkSizeFor(size: Long): Int = if (size > MAX_VIDEO) BIG_CHUNK else VIDEO_CHUNK
}

/** One decoded frame: a JSON object, plus raw bytes when it was a binary frame. */
class Frame(val json: JSONObject, val binary: ByteArray? = null) {
    val type: String get() = json.optString("t")
}

/**
 * An established, doubly-encrypted session over one socket.
 *
 * Reads and writes are blocking and belong on [Dispatchers.IO]; sends are
 * serialised so a media stream and a chat message cannot interleave mid-frame.
 */
class Session(
    private val input: InputStream,
    private val output: OutputStream,
    private val sharedKey: ByteArray,
    private val innerKey: ByteArray,
    val fingerprint: String,
    private val closer: () -> Unit,
) {
    private val reader = DataInputStream(input)
    private val sendLock = Mutex()

    suspend fun send(obj: JSONObject) =
        sendPayload(Protocol.KIND_JSON, obj.toString().toByteArray())

    /** Raw bytes with a small JSON header — no base64 inflation. */
    suspend fun sendBinary(header: JSONObject, blob: ByteArray, offset: Int = 0,
                           length: Int = blob.size) {
        val head = header.toString().toByteArray()
        val payload = ByteArray(4 + head.size + length)
        payload[0] = (head.size ushr 24).toByte()
        payload[1] = (head.size ushr 16).toByte()
        payload[2] = (head.size ushr 8).toByte()
        payload[3] = head.size.toByte()
        System.arraycopy(head, 0, payload, 4, head.size)
        System.arraycopy(blob, offset, payload, 4 + head.size, length)
        sendPayload(Protocol.KIND_BINARY, payload)
    }

    private suspend fun sendPayload(kind: Byte, payload: ByteArray) =
        withContext(Dispatchers.IO) {
            val tagged = ByteArray(Protocol.INNER_MAGIC.size + 1 + payload.size)
            System.arraycopy(Protocol.INNER_MAGIC, 0, tagged, 0, Protocol.INNER_MAGIC.size)
            tagged[Protocol.INNER_MAGIC.size] = kind
            System.arraycopy(payload, 0, tagged, Protocol.INNER_MAGIC.size + 1,
                             payload.size)
            val inner = Crypto.secretSeal(innerKey, tagged)
            val outer = Crypto.boxSeal(sharedKey, inner)
            sendLock.withLock {
                output.write(byteArrayOf(
                    (outer.size ushr 24).toByte(), (outer.size ushr 16).toByte(),
                    (outer.size ushr 8).toByte(), outer.size.toByte()))
                output.write(outer)
                output.flush()
            }
        }

    suspend fun receive(): Frame = withContext(Dispatchers.IO) {
        val length = reader.readInt()
        if (length <= 0 || length > Protocol.MAX_FRAME) error("bad frame length $length")
        val outer = ByteArray(length)
        reader.readFully(outer)
        val inner = Crypto.boxOpen(sharedKey, outer)
        val tagged = Crypto.secretOpen(innerKey, inner)
        val magicLen = Protocol.INNER_MAGIC.size
        for (i in 0 until magicLen) {
            if (tagged[i] != Protocol.INNER_MAGIC[i]) error("not a tor2 peer")
        }
        when (tagged[magicLen]) {
            Protocol.KIND_JSON ->
                Frame(JSONObject(String(tagged, magicLen + 1,
                                        tagged.size - magicLen - 1)))
            Protocol.KIND_BINARY -> {
                val body = tagged.copyOfRange(magicLen + 1, tagged.size)
                val headLen = ((body[0].toInt() and 0xff) shl 24) or
                        ((body[1].toInt() and 0xff) shl 16) or
                        ((body[2].toInt() and 0xff) shl 8) or
                        (body[3].toInt() and 0xff)
                if (headLen < 0 || headLen + 4 > body.size) error("bad binary header")
                Frame(JSONObject(String(body, 4, headLen)),
                      body.copyOfRange(4 + headLen, body.size))
            }
            else -> error("unknown payload kind")
        }
    }

    fun close() = runCatching { closer() }.let {}
}

/**
 * Exchange magic and ephemeral public keys, then derive both cipher layers.
 * The fingerprint is order-independent, so both ends read out the same code.
 */
suspend fun handshake(input: InputStream, output: OutputStream,
                      closer: () -> Unit): Session = withContext(Dispatchers.IO) {
    val keys = Crypto.generateKeyPair()
    output.write(Protocol.MAGIC)
    output.write(keys.public)
    output.flush()

    val reader = DataInputStream(input)
    val hello = ByteArray(Protocol.MAGIC.size + Crypto.KEY_BYTES)
    reader.readFully(hello)
    for (i in Protocol.MAGIC.indices) {
        if (hello[i] != Protocol.MAGIC[i]) {
            error("the other end is not speaking tor2 v4 — update both sides")
        }
    }
    val peerPublic = hello.copyOfRange(Protocol.MAGIC.size, hello.size)
    val shared = Crypto.sharedKey(keys.secret, peerPublic)
    val innerKey = Crypto.blake2bPersonal(shared, Protocol.INNER_PERSON)

    val a = keys.public
    val b = peerPublic
    val first: ByteArray
    val second: ByteArray
    if (compareBytes(a, b) <= 0) { first = a; second = b } else { first = b; second = a }
    val digest = Crypto.hex(
        Crypto.sha256("tor2-fp".toByteArray(), first, second)).substring(0, 16)
    val fingerprint = digest.chunked(4).joinToString("-")

    Session(input, output, shared, innerKey, fingerprint, closer)
}

private fun compareBytes(a: ByteArray, b: ByteArray): Int {
    for (i in 0 until minOf(a.size, b.size)) {
        val d = (a[i].toInt() and 0xff) - (b[i].toInt() and 0xff)
        if (d != 0) return d
    }
    return a.size - b.size
}
