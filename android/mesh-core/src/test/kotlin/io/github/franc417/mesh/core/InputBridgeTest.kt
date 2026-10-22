package io.github.franc417.mesh.core

import org.junit.Assert.assertEquals
import org.junit.Test

class InputBridgeTest {

    private class Recorder : InputSink {
        val calls = mutableListOf<String>()
        override fun move(dx: Float, dy: Float) { calls.add("move $dx $dy") }
        override fun scroll(vertical: Float, horizontal: Float) { calls.add("scroll $vertical $horizontal") }
        override fun button(button: Int, down: Boolean) { calls.add("button $button ${if (down) "down" else "up"}") }
        override fun key(code: String, down: Boolean) { calls.add("key $code ${if (down) "down" else "up"}") }
        override fun text(text: String) { calls.add("text $text") }
    }

    private fun run(vararg events: Map<String, Any?>): List<String> {
        val r = Recorder()
        val bridge = InputBridge(r)
        events.forEach { bridge.handle(it) }
        return r.calls
    }

    @Test
    fun `moves and scrolls pass through with any number type`() {
        assertEquals(listOf("move 1.5 -2.0"), run(mapOf("t" to "move", "dx" to 1.5f, "dy" to -2.0f)))
        assertEquals(listOf("move 3.0 4.0"), run(mapOf("t" to "move", "dx" to 3, "dy" to 4.0)))
        assertEquals(listOf("scroll 2.5 0.0"), run(mapOf("t" to "scroll", "dy" to 2.5f)))
        assertEquals(listOf("scroll 1.0 -1.0"), run(mapOf("t" to "scroll", "dy" to 1, "dx" to -1)))
    }

    @Test
    fun `a bare click is a press and a release`() {
        assertEquals(listOf("button 1 down", "button 1 up"), run(mapOf("t" to "click", "button" to "left")))
        assertEquals(listOf("button 3 down", "button 3 up"), run(mapOf("t" to "click", "button" to "right")))
    }

    @Test
    fun `click down and up are separate so drags work`() {
        assertEquals(
            listOf("button 1 down", "move 5.0 0.0", "button 1 up"),
            run(mapOf("t" to "click", "button" to "left", "action" to "down"), mapOf("t" to "move", "dx" to 5f, "dy" to 0f),
                mapOf("t" to "click", "button" to "left", "action" to "up")),
        )
    }

    @Test
    fun `a plain key tap is down then up`() {
        assertEquals(listOf("key KeyA down", "key KeyA up"), run(mapOf("t" to "key", "code" to "KeyA")))
    }

    @Test
    fun `modifiers are pressed around the key like a real keyboard`() {
        // The view sends down then up, each carrying the mods.
        assertEquals(
            listOf("key ControlLeft down", "key KeyC down", "key KeyC up", "key ControlLeft up"),
            run(mapOf("t" to "key", "code" to "KeyC", "action" to "down", "mods" to listOf("ctrl")),
                mapOf("t" to "key", "code" to "KeyC", "action" to "up", "mods" to listOf("ctrl"))),
        )
    }

    @Test
    fun `several modifiers are released in reverse order`() {
        val ev = { action: String -> mapOf("t" to "key", "code" to "ArrowLeft", "action" to action, "mods" to listOf("ctrl", "alt")) }
        assertEquals(
            listOf("key ControlLeft down", "key AltLeft down", "key ArrowLeft down", "key ArrowLeft up", "key AltLeft up", "key ControlLeft up"),
            run(ev("down"), ev("up")),
        )
    }

    @Test
    fun `a key with mods and no action is a whole press and release`() {
        assertEquals(
            listOf("key ShiftLeft down", "key Digit2 down", "key Digit2 up", "key ShiftLeft up"),
            run(mapOf("t" to "key", "code" to "Digit2", "mods" to listOf("shift"))),
        )
    }

    @Test
    fun `all four modifier names map to the left side keys`() {
        assertEquals(
            listOf("key ShiftLeft down", "key ControlLeft down", "key AltLeft down", "key MetaLeft down", "key KeyA down"),
            run(mapOf("t" to "key", "code" to "KeyA", "action" to "down", "mods" to listOf("shift", "ctrl", "alt", "meta"))),
        )
    }

    @Test
    fun `unknown modifiers are ignored and repeats are pressed once`() {
        assertEquals(
            listOf("key ControlLeft down", "key KeyA down"),
            run(mapOf("t" to "key", "code" to "KeyA", "action" to "down", "mods" to listOf("ctrl", "hyper", "ctrl", 7, null))),
        )
    }

    @Test
    fun `text events go through as text`() {
        assertEquals(listOf("text \u20AC"), run(mapOf("t" to "text", "text" to "\u20AC")))
    }

    @Test
    fun `malformed events are ignored without throwing`() {
        val bad = listOf<Map<String, Any?>>(
            emptyMap(), mapOf("t" to null), mapOf("t" to 5), mapOf("t" to "nonsense"),
            mapOf("t" to "move"), mapOf("t" to "move", "dx" to "1", "dy" to 2f), mapOf("t" to "move", "dx" to Float.NaN, "dy" to 1f),
            mapOf("t" to "move", "dx" to Float.POSITIVE_INFINITY, "dy" to 1f), mapOf("t" to "scroll"), mapOf("t" to "scroll", "dy" to "x"),
            mapOf("t" to "click"), mapOf("t" to "click", "button" to "laser"), mapOf("t" to "click", "button" to 1),
            mapOf("t" to "key"), mapOf("t" to "key", "code" to ""), mapOf("t" to "key", "code" to 5), mapOf("t" to "key", "code" to "K".repeat(100)),
            mapOf("t" to "text"), mapOf("t" to "text", "text" to ""), mapOf("t" to "text", "text" to 5),
        )
        assertEquals(emptyList<String>(), run(*bad.toTypedArray()))
    }

    @Test
    fun `a click needs a known button and a move needs both numbers`() {
        assertEquals(emptyList<String>(), run(mapOf("t" to "click", "action" to "down")))
        assertEquals(emptyList<String>(), run(mapOf("t" to "move", "dx" to 1f)))
    }

    @Test
    fun `the real client is a valid sink`() {
        val sink: InputSink = MipClient(MipClient.Config("127.0.0.1", 1, ByteArray(32), "a".repeat(64), "t"), object : MipClient.Listener {})
        // Sending on a client that never connected must be harmless (a touch before the connection is up).
        InputBridge(sink).handle(mapOf("t" to "move", "dx" to 1f, "dy" to 1f))
        InputBridge(sink).handle(mapOf("t" to "key", "code" to "KeyA"))
    }
}
