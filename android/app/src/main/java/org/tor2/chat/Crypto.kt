package org.tor2.chat

import com.goterl.lazysodium.LazySodium
import com.goterl.lazysodium.LazySodiumAndroid
import com.goterl.lazysodium.SodiumAndroid
import com.goterl.lazysodium.interfaces.Box
import com.goterl.lazysodium.interfaces.SecretBox
import com.goterl.lazysodium.utils.Key
import java.security.MessageDigest

/**
 * The same NaCl primitives the desktop client uses, so the two are wire
 * compatible: X25519 + XSalsa20-Poly1305 for the session layer, and a second
 * SecretBox layer keyed by BLAKE2b for the tor2-only inner layer.
 */
object Crypto {
    /** Swapped for the desktop build in unit tests; the phone uses the Android one. */
    var sodium: LazySodium = LazySodiumAndroid(SodiumAndroid())

    const val BOX_NONCE = Box.NONCEBYTES          // 24
    const val SECRETBOX_NONCE = SecretBox.NONCEBYTES
    const val KEY_BYTES = Box.PUBLICKEYBYTES      // 32

    class KeyPair(val secret: ByteArray, val public: ByteArray)

    fun generateKeyPair(): KeyPair {
        val pk = ByteArray(Box.PUBLICKEYBYTES)
        val sk = ByteArray(Box.SECRETKEYBYTES)
        if (!sodium.cryptoBoxKeypair(pk, sk)) error("key generation failed")
        return KeyPair(sk, pk)
    }

    /** X25519 shared secret, exactly as PyNaCl's Box computes it. */
    fun sharedKey(secret: ByteArray, peerPublic: ByteArray): ByteArray {
        val out = ByteArray(Box.BEFORENMBYTES)
        if (!sodium.cryptoBoxBeforeNm(out, peerPublic, secret)) {
            error("shared key derivation failed")
        }
        return out
    }

    /** Encrypt with a precomputed shared key; nonce is prepended, as PyNaCl does. */
    fun boxSeal(sharedKey: ByteArray, message: ByteArray): ByteArray {
        val nonce = randomBytes(BOX_NONCE)
        val cipher = ByteArray(message.size + Box.MACBYTES)
        if (!sodium.cryptoBoxEasyAfterNm(cipher, message, message.size.toLong(),
                                         nonce, sharedKey)) {
            error("encryption failed")
        }
        return nonce + cipher
    }

    fun boxOpen(sharedKey: ByteArray, framed: ByteArray): ByteArray {
        require(framed.size > BOX_NONCE + Box.MACBYTES) { "frame too short" }
        val nonce = framed.copyOfRange(0, BOX_NONCE)
        val cipher = framed.copyOfRange(BOX_NONCE, framed.size)
        val plain = ByteArray(cipher.size - Box.MACBYTES)
        if (!sodium.cryptoBoxOpenEasyAfterNm(plain, cipher, cipher.size.toLong(),
                                             nonce, sharedKey)) {
            error("decryption failed — wrong key or tampered frame")
        }
        return plain
    }

    fun secretSeal(key: ByteArray, message: ByteArray): ByteArray {
        val nonce = randomBytes(SECRETBOX_NONCE)
        val cipher = ByteArray(message.size + SecretBox.MACBYTES)
        if (!sodium.cryptoSecretBoxEasy(cipher, message, message.size.toLong(),
                                        nonce, key)) {
            error("inner encryption failed")
        }
        return nonce + cipher
    }

    fun secretOpen(key: ByteArray, framed: ByteArray): ByteArray {
        require(framed.size > SECRETBOX_NONCE + SecretBox.MACBYTES) { "frame too short" }
        val nonce = framed.copyOfRange(0, SECRETBOX_NONCE)
        val cipher = framed.copyOfRange(SECRETBOX_NONCE, framed.size)
        val plain = ByteArray(cipher.size - SecretBox.MACBYTES)
        if (!sodium.cryptoSecretBoxOpenEasy(plain, cipher, cipher.size.toLong(),
                                            nonce, key)) {
            error("inner decryption failed")
        }
        return plain
    }

    /** BLAKE2b with a personalisation string, matching hashlib.blake2b(person=…). */
    fun blake2bPersonal(input: ByteArray, person: ByteArray, outLen: Int = 32): ByteArray {
        val out = ByteArray(outLen)
        val personal = ByteArray(16)                 // libsodium pads to 16 bytes
        System.arraycopy(person, 0, personal, 0, minOf(person.size, 16))
        val salt = ByteArray(16)
        val rc = sodium.getSodium().crypto_generichash_blake2b_salt_personal(
            out, outLen, input, input.size.toLong(), null, 0, salt, personal)
        if (rc != 0) error("blake2b failed")
        return out
    }

    /** BLAKE2b-256 with no personalisation, as hashlib.blake2b defaults to. */
    fun blake2bPersonalRaw(input: ByteArray, outLen: Int = 32): ByteArray {
        val out = ByteArray(outLen)
        val rc = sodium.getSodium().crypto_generichash_blake2b_salt_personal(
            out, outLen, input, input.size.toLong(), null, 0,
            ByteArray(16), ByteArray(16))
        if (rc != 0) error("blake2b failed")
        return out
    }

    fun sha256(vararg parts: ByteArray): ByteArray {
        val md = MessageDigest.getInstance("SHA-256")
        parts.forEach { md.update(it) }
        return md.digest()
    }

    fun randomBytes(n: Int): ByteArray = sodium.randomBytesBuf(n)

    fun hex(bytes: ByteArray): String =
        bytes.joinToString("") { "%02x".format(it) }
}
