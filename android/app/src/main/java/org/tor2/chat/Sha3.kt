package org.tor2.chat

/**
 * SHA3-256, because Android does not have it.
 *
 * An onion address ends in a checksum over SHA3-256, and
 * `MessageDigest.getInstance("SHA3-256")` throws on Android even though it
 * works on a desktop JVM — which is why deriving an address from a code
 * failed on the phone while every test passed. Keccak is short and fully
 * specified, so it is implemented here rather than depending on the
 * platform.
 */
object Sha3 {

    private const val RATE = 136          // 1088 bits, for SHA3-256
    private const val OUT = 32

    private val ROUND_CONSTANTS = longArrayOf(
        0x0000000000000001uL.toLong(), 0x0000000000008082uL.toLong(),
        0x800000000000808AuL.toLong(), 0x8000000080008000uL.toLong(),
        0x000000000000808BuL.toLong(), 0x0000000080000001uL.toLong(),
        0x8000000080008081uL.toLong(), 0x8000000000008009uL.toLong(),
        0x000000000000008AuL.toLong(), 0x0000000000000088uL.toLong(),
        0x0000000080008009uL.toLong(), 0x000000008000000AuL.toLong(),
        0x000000008000808BuL.toLong(), 0x800000000000008BuL.toLong(),
        0x8000000000008089uL.toLong(), 0x8000000000008003uL.toLong(),
        0x8000000000008002uL.toLong(), 0x8000000000000080uL.toLong(),
        0x000000000000800AuL.toLong(), 0x800000008000000AuL.toLong(),
        0x8000000080008081uL.toLong(), 0x8000000000008080uL.toLong(),
        0x0000000080000001uL.toLong(), 0x8000000080008008uL.toLong(),
    )

    /** Rotation offsets, indexed x + 5*y. */
    private val R = intArrayOf(
        0, 1, 62, 28, 27,
        36, 44, 6, 55, 20,
        3, 10, 43, 25, 39,
        41, 45, 15, 21, 8,
        18, 2, 61, 56, 14,
    )

    fun digest(input: ByteArray): ByteArray {
        val state = LongArray(25)
        val padded = pad(input)
        var offset = 0
        while (offset < padded.size) {
            for (i in 0 until RATE / 8) {
                var lane = 0L
                for (b in 0 until 8) {
                    lane = lane or
                        ((padded[offset + i * 8 + b].toLong() and 0xff) shl (8 * b))
                }
                state[i] = state[i] xor lane
            }
            keccak(state)
            offset += RATE
        }

        val out = ByteArray(OUT)
        for (i in 0 until OUT) {
            out[i] = ((state[i / 8] ushr (8 * (i % 8))) and 0xff).toByte()
        }
        return out
    }

    private fun pad(input: ByteArray): ByteArray {
        val padLength = RATE - (input.size % RATE)
        val out = input.copyOf(input.size + padLength)
        out[input.size] = 0x06                     // SHA-3 domain separation
        out[out.size - 1] = (out[out.size - 1].toInt() or 0x80).toByte()
        return out
    }

    /** Keccak-f[1600], written the way the specification states it. */
    private fun keccak(a: LongArray) {
        val c = LongArray(5)
        val d = LongArray(5)
        val b = LongArray(25)
        for (round in 0 until 24) {
            for (x in 0 until 5) {
                c[x] = a[x] xor a[x + 5] xor a[x + 10] xor a[x + 15] xor a[x + 20]
            }
            for (x in 0 until 5) {
                d[x] = c[(x + 4) % 5] xor java.lang.Long.rotateLeft(c[(x + 1) % 5], 1)
            }
            for (y in 0 until 5) {
                for (x in 0 until 5) a[x + 5 * y] = a[x + 5 * y] xor d[x]
            }
            for (y in 0 until 5) {
                for (x in 0 until 5) {
                    b[y + 5 * ((2 * x + 3 * y) % 5)] =
                        java.lang.Long.rotateLeft(a[x + 5 * y], R[x + 5 * y])
                }
            }
            for (y in 0 until 5) {
                for (x in 0 until 5) {
                    a[x + 5 * y] = b[x + 5 * y] xor
                        (b[(x + 1) % 5 + 5 * y].inv() and b[(x + 2) % 5 + 5 * y])
                }
            }
            a[0] = a[0] xor ROUND_CONSTANTS[round]
        }
    }
}
