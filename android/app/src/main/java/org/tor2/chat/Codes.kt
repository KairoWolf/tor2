package org.tor2.chat

import java.security.MessageDigest

/**
 * Short codes that stand in for an onion address.
 *
 * Eight digits join a server, five pair a direct chat. Both ends derive the
 * same ed25519 key from the digits, so the code alone determines an address —
 * nothing is looked up anywhere, and there is nothing else to pass on.
 * Matches the desktop client byte for byte.
 */
object Codes {
    const val SERVER_PERSON = "tor2-server-join-v1"
    const val CHAT_PERSON = "tor2-rendezvous-v1"

    /** (expanded 64-byte secret for tor, 32-byte public key) */
    fun keyFor(code: String, person: String): Pair<ByteArray, ByteArray> {
        val seed = Crypto.blake2bPersonalRaw("$person:$code".toByteArray())
        val h = MessageDigest.getInstance("SHA-512").digest(seed)
        h[0] = (h[0].toInt() and 248).toByte()
        h[31] = (h[31].toInt() and 127).toByte()
        h[31] = (h[31].toInt() or 64).toByte()
        val pub = Ed25519.publicKeyNoClamp(h)
        return h to pub
    }

    /** The v3 onion address for a public key. */
    fun onionFor(pub: ByteArray): String {
        val version = byteArrayOf(3)
        // Android has no SHA3-256, so this is our own — see Sha3.
        val checksum = Sha3.digest(".onion checksum".toByteArray() + pub + version)
            .copyOfRange(0, 2)
        val encoded = base32(pub + checksum + version)
        return encoded.lowercase() + ".onion"
    }

    fun serverAddress(code: String): String = onionFor(keyFor(code, SERVER_PERSON).second)

    fun chatAddress(code: String): String = onionFor(keyFor(code, CHAT_PERSON).second)

    private const val ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

    private fun base32(data: ByteArray): String {
        val out = StringBuilder()
        var buffer = 0
        var bits = 0
        for (b in data) {
            buffer = (buffer shl 8) or (b.toInt() and 0xff)
            bits += 8
            while (bits >= 5) {
                out.append(ALPHABET[(buffer shr (bits - 5)) and 31])
                bits -= 5
            }
        }
        if (bits > 0) out.append(ALPHABET[(buffer shl (5 - bits)) and 31])
        return out.toString()
    }
}
