package org.tor2.chat

import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test

class ParallelTest {

    @Test
    fun `every byte is handed out exactly once`() = runBlocking {
        val size = 10_000_000L
        val plan = ChunkPlan(size, 1_000_000)
        val seen = mutableListOf<Pair<Long, Long>>()
        while (true) seen.add(plan.take() ?: break)
        assertEquals(size, seen.sumOf { it.second - it.first })
        assertEquals(0L, seen.first().first)
        assertEquals(size, seen.last().second)
        // no overlaps
        seen.sortBy { it.first }
        seen.zipWithNext().forEach { (a, b) -> assertEquals(a.second, b.first) }
    }

    @Test
    fun `several workers drain the plan without duplicating work`() = runBlocking {
        val plan = ChunkPlan(8_000_000, 250_000)
        val counted = java.util.concurrent.atomic.AtomicLong()
        val taken = java.util.Collections.synchronizedList(mutableListOf<Long>())
        coroutineScope {
            (1..6).map {
                async {
                    while (true) {
                        val piece = plan.take() ?: break
                        counted.addAndGet(piece.second - piece.first)
                        taken.add(piece.first)
                    }
                }
            }.awaitAll()
        }
        assertEquals(8_000_000L, counted.get())
        assertEquals(taken.size, taken.toSet().size)   // nothing sent twice
    }

    @Test
    fun `a failed piece goes back for another circuit`() = runBlocking {
        val plan = ChunkPlan(2_000_000, 1_000_000)
        val first = plan.take()!!
        plan.giveBack(first)
        assertEquals(first, plan.take())
        plan.take()
        assertEquals(null, plan.take())
    }
}
