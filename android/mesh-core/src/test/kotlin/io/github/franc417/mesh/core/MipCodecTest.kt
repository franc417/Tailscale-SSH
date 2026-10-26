package io.github.franc417.mesh.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The same byte vectors are asserted by the host agent's tests (tests/test_mesh_input.py): both sides speak identical bytes. */
class MipCodecTest {

    @Test
    fun `phone to host frames match the agent's vectors`() {
        assertEquals("0009023fc00000c0000000", MipCodec.move(1.5f, -2.0f).toHex())
        assertEquals("000903be80000000000000", MipCodec.scroll(-0.25f, 0f).toHex())
        assertEquals("0003040101", MipCodec.button(Mip.BUTTON_LEFT, true).toHex())
        assertEquals("00070501044b657941", MipCodec.key("KeyA", true).toHex())
        assertEquals("00040668c3a9", MipCodec.text("h\u00e9").toHex())
        assertEquals("00050700000007", MipCodec.ping(7).toHex())
        assertEquals("000108", MipCodec.releaseAll().toHex())
        assertEquals("0008010102aabb027068", MipCodec.hello(byteArrayOf(0xAA.toByte(), 0xBB.toByte()), "ph").toHex())
        assertEquals("000109", MipCodec.bye().toHex())
    }

    @Test
    fun `host to phone frames decode`() {
        assertEquals(HostMessage.HelloOk(1, 3, "h"), decode("00058101030168"))
        assertEquals(HostMessage.Error(1, "no"), decode("0005820102" + "6e6f"))
        assertEquals(HostMessage.Pong(7), decode("00058700000007"))
        assertEquals(HostMessage.Unknown(0x6F), decode("00026f01"))
    }

    private fun decode(frameHex: String): HostMessage {
        val bytes = frameHex.hexToBytes()
        val size = ((bytes[0].toInt() and 0xFF) shl 8) or (bytes[1].toInt() and 0xFF)
        assertEquals(size, bytes.size - 2)
        return MipCodec.decodeHost(bytes[2].toInt() and 0xFF, bytes.copyOfRange(3, bytes.size))
    }

    @Test
    fun `truncated host messages are rejected rather than misread`() {
        for (hex in listOf("0003810103", "00028201", "00038700 00".replace(" ", ""))) {
            val bytes = hex.hexToBytes()
            var failed = false
            try {
                MipCodec.decodeHost(bytes[2].toInt() and 0xFF, bytes.copyOfRange(3, bytes.size))
            } catch (e: IllegalArgumentException) {
                failed = true
            }
            assertTrue("$hex should have been rejected", failed)
        }
    }

    @Test
    fun `floats keep their fractions`() {
        val frame = MipCodec.move(0.3f, -0.7f)
        val buf = java.nio.ByteBuffer.wrap(frame, 3, 8)
        assertEquals(0.3f, buf.float, 0f)
        assertEquals(-0.7f, buf.float, 0f)
    }

    @Test
    fun `long text is split on character boundaries and nothing is lost`() {
        val text = "a".repeat(1500) + "\u20AC".repeat(500) + "\uD83D\uDE00".repeat(300)   // ascii, 3-byte, 4-byte (emoji)
        val chunks = MipCodec.textChunks(text)
        assertTrue(chunks.size > 1)
        for (c in chunks) assertTrue(c.toByteArray(Charsets.UTF_8).size <= Mip.MAX_TEXT_BYTES)
        assertEquals(text, chunks.joinToString(""))
        for (c in chunks) MipCodec.text(c)   // each fits a frame
    }

    @Test
    fun `empty text makes no chunks`() {
        assertEquals(emptyList<String>(), MipCodec.textChunks(""))
    }

    @Test
    fun `oversized frames and fields are refused when building`() {
        var failures = 0
        for (build in listOf<() -> Unit>({ MipCodec.text("x".repeat(Mip.MAX_TEXT_BYTES + 1)) }, { MipCodec.key("K".repeat(300), true) },
            { MipCodec.frame(1, ByteArray(Mip.MAX_FRAME)) })) {
            try { build() } catch (e: IllegalArgumentException) { failures++ }
        }
        assertEquals(3, failures)
    }

    @Test
    fun `hex helpers round trip`() {
        val data = byteArrayOf(0, 1, 127, -128, -1)
        assertEquals("00017f80ff", data.toHex())
        assertTrue(data.contentEquals("00017f80ff".hexToBytes()))
    }

    @Test
    fun `sunshine pairing frames match the agent's vectors`() {
        // The agent's own tests check the same bytes (tests/test_sunshine_link.py).
        assertEquals("000c0a043132333405706978656c", MipCodec.sunshinePair("1234", "pixel").toHex())
        assertEquals(HostMessage.SunshineResult(Mip.SUNSHINE_OK, "paired"), decode("00098a0006706169726564"))
        assertEquals(HostMessage.SunshineResult(Mip.SUNSHINE_NOT_CONFIGURED, "no"), decode("00058a01" + "02" + "6e6f"))
        assertTrue(HostMessage.SunshineResult(0, "").ok)
        assertFalse(HostMessage.SunshineResult(4, "").ok)
    }

    @Test
    fun `a sunshine pairing needs a four digit pin`() {
        for (bad in listOf("", "123", "12345", "12a4", "\u0661\u0662\u0663\u0664", " 123")) {
            var failed = false
            try {
                MipCodec.sunshinePair(bad, "phone")
            } catch (e: IllegalArgumentException) {
                failed = true
            }
            assertTrue("pin '$bad' should be refused", failed)
        }
    }

    @Test
    fun `a long phone name is cut so the frame always fits`() {
        val frame = MipCodec.sunshinePair("0000", "\uD83D\uDE00".repeat(200))
        assertTrue(frame.size <= 2 + Mip.MAX_FRAME)
    }

    @Test
    fun `truncated sunshine results are rejected rather than misread`() {
        for (hex in listOf("00028a00", "00048a0005" + "6162")) {
            val bytes = hex.hexToBytes()
            var failed = false
            try {
                MipCodec.decodeHost(bytes[2].toInt() and 0xFF, bytes.copyOfRange(3, bytes.size))
            } catch (e: IllegalArgumentException) {
                failed = true
            }
            assertTrue("should reject $hex", failed)
        }
    }

    @Test
    fun `an absolute move matches the agent's vector and clamps its input`() {
        // The agent's own tests check the same bytes (tests/test_absolute_pointer.py).
        assertEquals("00050b0000ffff", MipCodec.moveAbs(0f, 1f).toHex())
        assertEquals("00050b80008000", MipCodec.moveAbs(0.5f, 0.5f).toHex())
        assertEquals(0x8000, MipCodec.toWire(0.5f))
        assertEquals(0, MipCodec.toWire(-3f))
        assertEquals(Mip.ABS_MAX, MipCodec.toWire(7f))
        assertEquals(Mip.ABS_MAX, MipCodec.toWire(Float.POSITIVE_INFINITY))
        assertEquals(0, MipCodec.toWire(Float.NEGATIVE_INFINITY))
        assertEquals(0, MipCodec.toWire(Float.NaN))
    }
}
