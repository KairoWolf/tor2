package org.tor2.chat

import com.goterl.lazysodium.LazySodiumJava
import com.goterl.lazysodium.SodiumJava
import com.goterl.lazysodium.interfaces.Box
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The Android client must derive byte-identical keys to the desktop one, or
 * nothing will connect. These vectors were produced by the Python client
 * (PyNaCl + hashlib.blake2b) and are checked here against libsodium, which is
 * what the app uses on the phone.
 */
class CryptoVectorsTest {

    private val sodium = LazySodiumJava(SodiumJava())
    private val vectors: JSONObject by lazy {
        JSONObject(javaClass.classLoader!!
            .getResourceAsStream("vectors.json")!!
            .bufferedReader().readText())
    }

    private fun hex(s: String): ByteArray =
        s.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    private fun toHex(b: ByteArray): String = b.joinToString("") { "%02x".format(it) }

    @Test
    fun `x25519 shared secret matches PyNaCl`() {
        val aSecret = hex(vectors.getString("a_secret"))
        val bPublic = hex(vectors.getString("b_public"))
        val shared = ByteArray(Box.BEFORENMBYTES)
        assert(sodium.cryptoBoxBeforeNm(shared, bPublic, aSecret))
        assertEquals(vectors.getString("shared"), toHex(shared))
    }

    @Test
    fun `inner layer key matches hashlib blake2b with personalisation`() {
        val shared = hex(vectors.getString("shared"))
        val person = ByteArray(16)
        "tor2-inner-v1".toByteArray().copyInto(person)
        val out = ByteArray(32)
        val rc = sodium.getSodium().crypto_generichash_blake2b_salt_personal(
            out, 32, shared, shared.size.toLong(), null, 0, ByteArray(16), person)
        assertEquals(0, rc)
        assertEquals(vectors.getString("inner_key"), toHex(out))
    }

    @Test
    fun `session fingerprint matches the desktop client`() {
        val a = hex(vectors.getString("a_public"))
        val b = hex(vectors.getString("b_public"))
        val (first, second) = if (compare(a, b) <= 0) a to b else b to a
        val md = java.security.MessageDigest.getInstance("SHA-256")
        md.update("tor2-fp".toByteArray()); md.update(first); md.update(second)
        val fp = toHex(md.digest()).substring(0, 16).chunked(4).joinToString("-")
        assertEquals(vectors.getString("fingerprint"), fp)
    }

    private fun compare(a: ByteArray, b: ByteArray): Int {
        for (i in 0 until minOf(a.size, b.size)) {
            val d = (a[i].toInt() and 0xff) - (b[i].toInt() and 0xff)
            if (d != 0) return d
        }
        return a.size - b.size
    }
}
