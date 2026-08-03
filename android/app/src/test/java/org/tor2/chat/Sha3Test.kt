package org.tor2.chat

import org.junit.Assert.assertEquals
import org.junit.Test
import java.security.MessageDigest

/**
 * Checked against the published SHA3-256 vectors and against the JVM's own
 * implementation — Android lacks it, which is why this exists at all.
 */
class Sha3Test {

    private fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }

    @Test
    fun `matches the published vectors`() {
        assertEquals(
            "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a",
            hex(Sha3.digest(ByteArray(0))))
        assertEquals(
            "3a985da74fe225b2045c172d6bd390bd855f086e3e9d525b46bfe24511431532",
            hex(Sha3.digest("abc".toByteArray())))
        assertEquals(
            "41c0dba2a9d6240849100376a8235e2c82e1b9998a999e21db32dd97496d3376",
            hex(Sha3.digest(
                "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"
                    .toByteArray())))
    }

    @Test
    fun `matches the platform implementation across sizes`() {
        val jvm = MessageDigest.getInstance("SHA3-256")
        for (size in intArrayOf(1, 55, 63, 64, 135, 136, 137, 200, 1000, 4096)) {
            val data = ByteArray(size) { (it * 31 % 251).toByte() }
            assertEquals("size $size", hex(jvm.digest(data)), hex(Sha3.digest(data)))
        }
    }
}
