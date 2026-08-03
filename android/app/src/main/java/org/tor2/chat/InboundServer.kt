package org.tor2.chat

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import java.net.ServerSocket

/**
 * Listens behind this phone's onion service so other people can start a chat
 * with it. Nothing is shown to the user until a valid tor2 handshake and hello
 * arrive, and nothing is processed until they accept.
 */
class InboundServer(
    private val scope: CoroutineScope,
    private val onRequest: (Session, org.json.JSONObject) -> Unit,
) {
    var port: Int = 0
        private set
    private var socket: ServerSocket? = null

    fun start() {
        scope.launch(Dispatchers.IO) {
            val server = ServerSocket(0, 16, java.net.InetAddress.getByName("127.0.0.1"))
            socket = server
            port = server.localPort
            while (scope.isActive && !server.isClosed) {
                val client = runCatching { server.accept() }.getOrNull() ?: continue
                scope.launch(Dispatchers.IO) {
                    runCatching {
                        val session = handshake(client.getInputStream(),
                                                client.getOutputStream()) {
                            runCatching { client.close() }
                        }
                        val hello = withTimeout(60_000) { session.receive() }
                        if (hello.type != "hello") {
                            session.close()
                        } else {
                            withContext(Dispatchers.Main) {
                                onRequest(session, hello.json)
                            }
                        }
                    }.onFailure { runCatching { client.close() } }
                }
            }
        }
    }

    fun stop() {
        runCatching { socket?.close() }
        socket = null
    }
}
