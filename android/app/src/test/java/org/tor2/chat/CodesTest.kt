package org.tor2.chat

import com.goterl.lazysodium.LazySodiumJava
import com.goterl.lazysodium.SodiumJava
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * A code must resolve to the same address on the phone as on the desktop, or
 * typing it here would reach nothing. The expected values come from the
 * Python client.
 */
class CodesTest {

    @Before
    fun useDesktopSodium() {
        Crypto.sodium = LazySodiumJava(SodiumJava())
    }

    private val vectors: JSONObject by lazy {
        JSONObject(javaClass.classLoader!!.getResourceAsStream("codes.json")!!
            .bufferedReader().readText())
    }

    @Test
    fun `server join codes match the desktop client`() {
        val cases = vectors.getJSONObject("server")
        for (code in cases.keys()) {
            assertEquals("code $code", cases.getString(code),
                         Codes.serverAddress(code))
        }
    }

    @Test
    fun `direct chat pairing codes match the desktop client`() {
        val cases = vectors.getJSONObject("chat")
        for (code in cases.keys()) {
            assertEquals("code $code", cases.getString(code),
                         Codes.chatAddress(code))
        }
    }

    @Test
    fun `the two kinds of code never collide`() {
        assert(Codes.serverAddress("12345678") != Codes.chatAddress("12345678"))
    }
}
