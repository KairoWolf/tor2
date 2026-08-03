package org.tor2.chat

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * Splitting a transfer across several Tor circuits.
 *
 * Tor gives a connection its own circuit when its SOCKS credentials differ, so
 * several named streams to one service are not sharing one circuit's
 * bandwidth. Pieces are handed out on demand rather than in equal shares:
 * circuit speeds vary a lot, and an even split would end the transfer only
 * when the slowest one finished while the others idled.
 */
class ChunkPlan(size: Long, piece: Int) {
    private val pieces = ArrayDeque<Pair<Long, Long>>()
    private val lock = Mutex()

    init {
        var pos = 0L
        while (pos < size) {
            val next = minOf(pos + piece, size)
            pieces.addLast(pos to next)
            pos = next
        }
    }

    suspend fun take(): Pair<Long, Long>? = lock.withLock { pieces.removeFirstOrNull() }

    /** A circuit died mid-piece; let another one pick up what is left. */
    suspend fun giveBack(piece: Pair<Long, Long>) = lock.withLock {
        pieces.addFirst(piece)
    }

    suspend fun isEmpty(): Boolean = lock.withLock { pieces.isEmpty() }
}

object Parallel {
    const val MIN_SIZE = 4L * 1024 * 1024      // below this, one circuit is fine

    /**
     * Open extra authenticated sessions to a server, each on its own circuit.
     * Returns however many succeeded — none is a normal outcome, and callers
     * fall back to the single-circuit path.
     */
    suspend fun extraCircuits(tor: TorNet, onion: String, token: String,
                              nick: String, count: Int): List<Session> =
        withContext(Dispatchers.IO) {
            if (count <= 0 || token.isBlank()) return@withContext emptyList()
            coroutineScope {
                (0 until count).map { i ->
                    async {
                        runCatching {
                            val (socket, input, output) =
                                tor.dial(onion, stream = "tor2-x$i")
                            val s = handshake(input, output) {
                                runCatching { socket.close() }
                            }
                            if (s.receive().type != "srvhello") error("not a server")
                            s.send(JSONObject().apply {
                                put("t", "auth"); put("nick", nick); put("token", token)
                            })
                            if (s.receive().type != "authok") error("auth refused")
                            s
                        }.getOrNull()
                    }
                }.awaitAll().filterNotNull()
            }
        }

    /** Push a file up several circuits at once. */
    suspend fun upload(sessions: List<Session>, bytes: ByteArray, chunk: Int,
                       onBytes: suspend (Int) -> Unit) = coroutineScope {
        val plan = ChunkPlan(bytes.size.toLong(), chunk)
        sessions.map { s ->
            async(Dispatchers.IO) {
                while (true) {
                    val piece = plan.take() ?: break
                    var pos = piece.first
                    try {
                        while (pos < piece.second) {
                            val n = minOf(chunk.toLong(), piece.second - pos).toInt()
                            s.sendBinary(JSONObject().apply {
                                put("t", "mchunk"); put("off", pos)
                            }, bytes, pos.toInt(), n)
                            pos += n
                            onBytes(n)
                        }
                    } catch (e: Exception) {
                        plan.giveBack(pos to piece.second)
                        throw e
                    }
                }
            }
        }.awaitAll()
        check(plan.isEmpty()) { "not every piece was sent" }
    }
}
