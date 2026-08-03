package org.tor2.chat

import com.goterl.lazysodium.LazySodiumJava
import com.goterl.lazysodium.SodiumJava
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.PipedInputStream
import java.io.PipedOutputStream

/**
 * Frames written by this client must be readable by it (and, by construction,
 * by the desktop client, which uses the same layout: length prefix, outer box,
 * inner box, magic, kind byte, then either JSON or a JSON header plus raw
 * bytes).
 */
class FramingTest {

    @Before
    fun useDesktopSodium() {
        Crypto.sodium = LazySodiumJava(SodiumJava())
    }

    private fun session(out: ByteArrayOutputStream, key: ByteArray, inner: ByteArray) =
        Session(ByteArrayInputStream(ByteArray(0)), out, key, inner, "fp") {}

    @Test
    fun `json frame round trips`() = runBlocking {
        val key = Crypto.randomBytes(32)
        val inner = Crypto.randomBytes(32)
        val out = ByteArrayOutputStream()
        session(out, key, inner).send(JSONObject().apply {
            put("t", "txt"); put("body", "hello over tor")
        })
        val back = Session(ByteArrayInputStream(out.toByteArray()),
                           ByteArrayOutputStream(), key, inner, "fp") {}
        val frame = back.receive()
        assertEquals("txt", frame.type)
        assertEquals("hello over tor", frame.json.getString("body"))
        assertEquals(null, frame.binary)
    }

    @Test
    fun `binary frame carries raw bytes with no base64 inflation`() = runBlocking {
        val key = Crypto.randomBytes(32)
        val inner = Crypto.randomBytes(32)
        val payload = ByteArray(300_000) { (it % 251).toByte() }
        val out = ByteArrayOutputStream()
        session(out, key, inner).sendBinary(
            JSONObject().apply { put("t", "mchunk"); put("off", 4096) }, payload)

        // the frame must be close to the payload size, not a third larger
        val overhead = out.size() - payload.size
        assert(overhead < 2000) { "binary frame added $overhead bytes of overhead" }

        val back = Session(ByteArrayInputStream(out.toByteArray()),
                           ByteArrayOutputStream(), key, inner, "fp") {}
        val frame = back.receive()
        assertEquals("mchunk", frame.type)
        assertEquals(4096, frame.json.getInt("off"))
        assertArrayEquals(payload, frame.binary)
    }

    @Test
    fun `a frame encrypted with the wrong inner key is rejected`() = runBlocking {
        val key = Crypto.randomBytes(32)
        val out = ByteArrayOutputStream()
        session(out, key, Crypto.randomBytes(32))
            .send(JSONObject().put("t", "txt").put("body", "x"))
        val back = Session(ByteArrayInputStream(out.toByteArray()),
                           ByteArrayOutputStream(), key, Crypto.randomBytes(32), "fp") {}
        val threw = runCatching { back.receive() }.isFailure
        assert(threw) { "a frame with the wrong inner key must not decode" }
    }

    @Test
    fun `handshake between two ends agrees on the fingerprint`() = runBlocking {
        val aToB = PipedOutputStream()
        val bFromA = PipedInputStream(aToB, 1 shl 16)
        val bToA = PipedOutputStream()
        val aFromB = PipedInputStream(bToA, 1 shl 16)

        val bSide = Thread {
            runBlocking { bSession = handshake(bFromA, bToA) {} }
        }.also { it.start() }
        val a = handshake(aFromB, aToB) {}
        bSide.join(5000)

        assertEquals(a.fingerprint, bSession!!.fingerprint)
        assert(Regex("^[0-9a-f]{4}(-[0-9a-f]{4}){3}$").matches(a.fingerprint)) {
            "unexpected fingerprint shape: ${a.fingerprint}"
        }
    }

    private var bSession: Session? = null
}
