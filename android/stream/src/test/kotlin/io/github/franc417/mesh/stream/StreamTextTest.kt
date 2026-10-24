package io.github.franc417.mesh.stream

import com.limelight.nvstream.jni.MoonBridge
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class StreamTextTest {
    @Test
    fun errorConstantsMatchMoonBridge() {
        assertEquals(MoonBridge.ML_ERROR_GRACEFUL_TERMINATION, StreamText.ERROR_GRACEFUL_TERMINATION)
        assertEquals(MoonBridge.ML_ERROR_NO_VIDEO_TRAFFIC, StreamText.ERROR_NO_VIDEO_TRAFFIC)
        assertEquals(MoonBridge.ML_ERROR_NO_VIDEO_FRAME, StreamText.ERROR_NO_VIDEO_FRAME)
        assertEquals(MoonBridge.ML_ERROR_UNEXPECTED_EARLY_TERMINATION, StreamText.ERROR_UNEXPECTED_EARLY_TERMINATION)
        assertEquals(MoonBridge.ML_ERROR_PROTECTED_CONTENT, StreamText.ERROR_PROTECTED_CONTENT)
        assertEquals(MoonBridge.ML_ERROR_FRAME_CONVERSION, StreamText.ERROR_FRAME_CONVERSION)
    }

    @Test
    fun portFlagsMatchMoonBridge() {
        assertEquals("TCP 47984", StreamText.portList(MoonBridge.ML_PORT_FLAG_TCP_47984))
        assertEquals("TCP 47989", StreamText.portList(MoonBridge.ML_PORT_FLAG_TCP_47989))
        assertEquals("TCP 48010", StreamText.portList(MoonBridge.ML_PORT_FLAG_TCP_48010))
        assertEquals("UDP 47998", StreamText.portList(MoonBridge.ML_PORT_FLAG_UDP_47998))
        assertEquals("UDP 47999", StreamText.portList(MoonBridge.ML_PORT_FLAG_UDP_47999))
        assertEquals("UDP 48000", StreamText.portList(MoonBridge.ML_PORT_FLAG_UDP_48000))
        assertEquals("UDP 48010", StreamText.portList(MoonBridge.ML_PORT_FLAG_UDP_48010))
    }

    @Test
    fun portListJoinsSeveralAndIgnoresUnknownBits() {
        val flags = MoonBridge.ML_PORT_FLAG_TCP_47984 or MoonBridge.ML_PORT_FLAG_TCP_47989 or 0x40000
        assertEquals("TCP 47984, TCP 47989", StreamText.portList(flags))
        assertEquals("", StreamText.portList(0))
    }

    @Test
    fun gracefulTerminationIsNotAnError() {
        assertNull(StreamText.termination(0))
    }

    @Test
    fun everyKnownTerminationHasASpecificMessage() {
        val codes = listOf(-100, -101, -102, -103, -104)
        val messages = codes.map { assertNotNull(StreamText.termination(it)); StreamText.termination(it)!! }
        assertEquals("each code has its own text", codes.size, messages.toSet().size)
        assertTrue(StreamText.termination(-100)!!.contains("47998"))
    }

    @Test
    fun unknownTerminationShowsTheCode() {
        assertTrue(StreamText.termination(-7)!!.contains("-7"))
    }

    @Test
    fun stageFailureNamesTheStageErrorAndPorts() {
        val text = StreamText.stageFailure("RTSP handshake", MoonBridge.ML_PORT_FLAG_TCP_48010, 60)
        assertTrue(text.contains("RTSP handshake"))
        assertTrue(text.contains("60"))
        assertTrue(text.contains("TCP 48010"))
    }

    @Test
    fun stageFailureWithoutDetailsStillReads() {
        val text = StreamText.stageFailure("", 0, 0)
        assertEquals("Failed while starting the stream.", text)
    }

    @Test
    fun settingsValidateTheirRanges() {
        StreamSettings()
        for (bad in listOf<() -> StreamSettings>(
            { StreamSettings(width = 0) },
            { StreamSettings(fps = 5) },
            { StreamSettings(bitrateKbps = 100) },
        )) {
            try {
                bad()
                throw AssertionError("expected IllegalArgumentException")
            } catch (_: IllegalArgumentException) {
            }
        }
    }

    @Test
    fun presetsAreValidAndFirstOneIsTheDefault() {
        assertEquals(StreamSettings(), StreamSettings.presets.first().second)
        assertTrue(StreamSettings.presets.map { it.first }.toSet().size == StreamSettings.presets.size)
    }
}
