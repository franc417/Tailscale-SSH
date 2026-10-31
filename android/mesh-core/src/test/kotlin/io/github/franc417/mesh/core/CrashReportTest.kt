package io.github.franc417.mesh.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CrashReportTest {

    private fun report(trace: String) = CrashReport.format("2026-10-31 16:30:00", "0.1.0", "16 (API 36)", "Xiaomi 25128RN17A", "main", trace)

    @Test
    fun theReportNamesThePhoneTheAppAndTheThread() {
        val text = report("java.lang.IllegalStateException: boom\n\tat a.b.C.d(C.kt:1)\n")
        val lines = text.lines()
        assertEquals("Mesh crash report", lines[0])
        assertTrue(lines.contains("device: Xiaomi 25128RN17A"))
        assertTrue(lines.contains("thread: main"))
        assertTrue(lines.contains("android: 16 (API 36)"))
        assertTrue(text.contains("IllegalStateException: boom"))
    }

    @Test
    fun aShortTraceIsKeptWhole() {
        val trace = "java.lang.RuntimeException: x\n\tat a.B.c(B.kt:2)"
        assertEquals(trace, CrashReport.shorten(trace, 6000))
        assertFalse(report(trace).contains("cut from the middle"))
    }

    @Test
    fun aLongTraceKeepsItsBeginningAndTheRootCauseAtTheEnd() {
        val filler = "\tat android.framework.Frame.run(Frame.java:1)\n".repeat(400)
        val trace = "java.lang.RuntimeException: outer\n" + filler + "Caused by: javax.net.ssl.SSLException: the real reason\n\tat x.Y.z(Y.kt:9)"
        val text = report(trace)
        assertTrue(text.contains("RuntimeException: outer"))
        assertTrue(text.contains("Caused by: javax.net.ssl.SSLException: the real reason"))
        assertTrue(text.contains("characters cut from the middle"))
        assertTrue(text.length < CrashReport.MAX_TRACE_CHARS + 400)
    }

    @Test
    fun theLengthStaysWithinTheLimitEvenWhenTheNoticeIsAdded() {
        val cut = CrashReport.shorten("a".repeat(20000), 6000)
        assertTrue(cut.length < 6100)
        assertTrue(cut.startsWith("aaaa") && cut.endsWith("aaaa"))
    }
}
