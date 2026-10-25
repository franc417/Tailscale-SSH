package io.github.franc417.mesh.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SunshineWaiterTest {
    private fun result(status: Int, text: String = "") = HostMessage.SunshineResult(status, text)

    @Test
    fun `the answer reaches the waiting request exactly once`() {
        val waiter = SunshineWaiter()
        val got = mutableListOf<HostMessage.SunshineResult>()
        waiter.start { got += it }
        waiter.deliver(result(0, "paired"))
        waiter.deliver(result(0, "again"))   // a stray second answer is ignored
        assertEquals(listOf(result(0, "paired")), got)
    }

    @Test
    fun `an answer with nobody waiting is ignored`() {
        SunshineWaiter().deliver(result(0))   // must not throw
    }

    @Test
    fun `a new request ends the old one and only the new one gets the answer`() {
        val waiter = SunshineWaiter()
        val first = mutableListOf<HostMessage.SunshineResult>()
        val second = mutableListOf<HostMessage.SunshineResult>()
        waiter.start { first += it }
        waiter.start { second += it }
        assertEquals(1, first.size)
        assertEquals(Mip.SUNSHINE_FAILED, first[0].status)
        assertTrue(first[0].text.contains("replaced"))
        assertTrue(second.isEmpty())
        waiter.deliver(result(0, "paired"))
        assertEquals(listOf(result(0, "paired")), second)
        assertEquals(1, first.size)
    }

    @Test
    fun `the request can be used again after it was answered`() {
        val waiter = SunshineWaiter()
        val got = mutableListOf<Int>()
        waiter.start { got += it.status }
        waiter.deliver(result(4))
        waiter.start { got += it.status }
        waiter.deliver(result(0))
        assertEquals(listOf(4, 0), got)
    }

    @Test
    fun `a lost connection is reported to the waiting request`() {
        val waiter = SunshineWaiter()
        var status = 99
        waiter.start { status = it.status }
        waiter.deliver(result(Mip.SUNSHINE_CONNECTION_LOST, "ended"))
        assertEquals(Mip.SUNSHINE_CONNECTION_LOST, status)
    }
}
