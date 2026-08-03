package org.tor2.chat

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.withContext
import net.freehaven.tor.control.TorControlConnection
import org.torproject.jni.TorService
import java.io.DataInputStream
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket

/**
 * Runs Tor on the phone and dials onion services through it.
 *
 * The same trick the desktop client uses for speed applies here: giving a
 * connection distinct SOCKS credentials puts it on its own circuit, so several
 * named streams to one service transfer in parallel instead of sharing one
 * circuit's bandwidth.
 */
class TorNet(private val context: Context) {

    val status = MutableStateFlow("Starting Tor…")
    val bootstrapPercent = MutableStateFlow(0)
    val myOnion = MutableStateFlow<String?>(null)

    /** Discovered from the service — never assume the default port. */
    @Volatile var socksPort: Int = 0
    private var control: TorControlConnection? = null
    private var serviceId: String? = null

    /**
     * Wait until Tor is genuinely usable, reporting Tor's own bootstrap
     * percentage rather than a made-up one that stalls short of the end.
     */
    suspend fun awaitReady(timeoutMs: Long = 300_000) = withContext(Dispatchers.IO) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (socksPort <= 0) socksPort = discoverSocksPort()
            readBootstrapPhase()?.let { (percent, summary) ->
                bootstrapPercent.value = percent
                if (summary.isNotBlank()) status.value = summary
            }
            if (socksPort > 0 && probeSocks()) {
                bootstrapPercent.value = 100
                status.value = "Connected"
                return@withContext
            }
            Thread.sleep(700)
        }
        error("Tor did not finish starting — check the phone's connection")
    }

    /** The service picks its own port; ask rather than guessing 9050. */
    private fun discoverSocksPort(): Int {
        TorService.socksPort.takeIf { it > 0 }?.let { return it }
        val info = runCatching { control?.getInfo("net/listeners/socks") }.getOrNull()
        Regex(":(\\d+)").find(info ?: "")?.groupValues?.get(1)?.toIntOrNull()
            ?.let { return it }
        return 0
    }

    /** Real progress, straight from Tor's own status. */
    private fun readBootstrapPhase(): Pair<Int, String>? {
        val line = runCatching {
            control?.getInfo("status/bootstrap-phase")
        }.getOrNull() ?: return null
        val percent = Regex("PROGRESS=(\\d+)").find(line)?.groupValues?.get(1)
            ?.toIntOrNull() ?: return null
        val summary = Regex("SUMMARY=\"([^\"]*)\"").find(line)?.groupValues?.get(1) ?: ""
        return percent to summary
    }

    private fun probeSocks(): Boolean = runCatching {
        Socket().use {
            it.connect(InetSocketAddress("127.0.0.1", socksPort), 1500)
            true
        }
    }.getOrDefault(false)

    fun attachControl(conn: TorControlConnection) {
        control = conn
    }

    /**
     * Publish an onion service pointing at a local port, so other people can
     * start a chat with this phone. Reuses a saved key so the address stays
     * the same, which is what makes it worth sharing with a friend.
     */
    suspend fun publishOnion(localPort: Int, keyBlob: String?): Pair<String, String>? =
        withContext(Dispatchers.IO) {
            val conn = control ?: return@withContext null
            val keyArg = keyBlob?.takeIf { it.isNotBlank() } ?: "NEW:ED25519-V3"
            val ports = HashMap<Int, String>()
            ports[80] = "127.0.0.1:$localPort"
            val reply = runCatching { conn.addOnion(keyArg, ports, null) }
                .getOrNull() ?: return@withContext null
            val id = reply["ServiceID"] ?: return@withContext null
            val priv = reply["PrivateKey"] ?: keyBlob ?: ""
            serviceId = id
            myOnion.value = "$id.onion"
            Pair("$id.onion", priv)
        }

    /** Publish the address a short code derives, so the digits reach us. */
    suspend fun publishCodeOnion(code: String, localPort: Int, person: String):
            String? = withContext(Dispatchers.IO) {
        val conn = control ?: return@withContext null
        val (expanded, pub) = Codes.keyFor(code, person)
        val blob = "ED25519-V3:" + android.util.Base64.encodeToString(
            expanded, android.util.Base64.NO_WRAP)
        val ports = HashMap<Int, String>()
        ports[80] = "127.0.0.1:$localPort"
        val reply = runCatching { conn.addOnion(blob, ports, null) }.getOrNull()
            ?: return@withContext null
        val id = reply["ServiceID"] ?: return@withContext null
        codeServices[code] = id
        val expected = Codes.onionFor(pub).removeSuffix(".onion")
        if (id != expected) {          // sanity: our maths must match tor's
            runCatching { conn.delOnion(id) }
            codeServices.remove(code)
            return@withContext null
        }
        "$id.onion"
    }

    suspend fun unpublishCode(code: String) = withContext(Dispatchers.IO) {
        codeServices.remove(code)?.let { runCatching { control?.delOnion(it) } }
        Unit
    }

    private val codeServices = mutableMapOf<String, String>()

    fun unpublishOnion() {
        val id = serviceId ?: return
        runCatching { control?.delOnion(id) }
        serviceId = null
        myOnion.value = null
    }

    /**
     * Connect to an onion address. [stream] names an isolation group: distinct
     * names get distinct circuits.
     */
    suspend fun dial(onion: String, port: Int = 80, stream: String = ""):
            Triple<Socket, InputStream, OutputStream> = withContext(Dispatchers.IO) {
        check(socksPort > 0) { "Tor is not ready yet" }
        val socket = Socket()
        socket.connect(InetSocketAddress("127.0.0.1", socksPort), 15_000)
        socket.soTimeout = 300_000
        val out = socket.getOutputStream()
        val inp = socket.getInputStream()
        val reader = DataInputStream(inp)

        if (stream.isNotEmpty()) {
            val user = stream.toByteArray().copyOf(minOf(stream.length, 255))
            out.write(byteArrayOf(0x05, 0x01, 0x02))
            out.flush()
            val greet = ByteArray(2).also { reader.readFully(it) }
            if (greet[1] != 0x02.toByte()) error("tor refused SOCKS authentication")
            out.write(byteArrayOf(0x01, user.size.toByte()))
            out.write(user)
            out.write(byteArrayOf(user.size.toByte()))
            out.write(user)
            out.flush()
            val auth = ByteArray(2).also { reader.readFully(it) }
            if (auth[1] != 0x00.toByte()) error("SOCKS authentication rejected")
        } else {
            out.write(byteArrayOf(0x05, 0x01, 0x00))
            out.flush()
            val greet = ByteArray(2).also { reader.readFully(it) }
            if (greet[0] != 0x05.toByte() || greet[1] != 0x00.toByte()) {
                error("SOCKS handshake refused")
            }
        }

        val host = onion.removeSuffix(".onion").plus(".onion").toByteArray()
        out.write(byteArrayOf(0x05, 0x01, 0x00, 0x03, host.size.toByte()))
        out.write(host)
        out.write(byteArrayOf((port ushr 8).toByte(), port.toByte()))
        out.flush()

        val resp = ByteArray(4).also { reader.readFully(it) }
        if (resp[1] != 0x00.toByte()) {
            socket.close()
            error(socksError(resp[1].toInt() and 0xff))
        }
        when (resp[3].toInt()) {
            0x01 -> reader.readFully(ByteArray(6))
            0x03 -> {
                val n = reader.readUnsignedByte()
                reader.readFully(ByteArray(n + 2))
            }
            0x04 -> reader.readFully(ByteArray(18))
        }
        Triple(socket, inp, out)
    }

    private fun socksError(code: Int) = when (code) {
        1 -> "tor could not reach that address (is the other side running?)"
        4 -> "onion address not found — check it was typed correctly"
        else -> "tor could not connect (SOCKS code $code)"
    }

    companion object {
        fun dataDir(context: Context): File =
            File(context.filesDir, "tor").apply { mkdirs() }
    }
}
