package io.github.franc417.mesh.core

/** Where input goes. MipClient is the real one; tests use a recorder. */
interface InputSink {
    fun move(dx: Float, dy: Float)
    fun scroll(vertical: Float, horizontal: Float)
    fun button(button: Int, down: Boolean)
    fun key(code: String, down: Boolean)
    fun text(text: String)
}

/**
 * Turns what the trackpad and keyboard views emit (small maps such as
 * {"t":"key","code":"KeyC","action":"down","mods":["ctrl"]}) into input-channel calls.
 *
 * The views were written for a protocol where modifiers ride along with a key; the host
 * works like a real keyboard, where each modifier is its own key. So a key event with
 * mods becomes: modifiers down, key down ... key up, modifiers up (in reverse order), which is
 * exactly what pressing Ctrl+C on a physical keyboard does. Malformed events are ignored, never thrown on:
 * a stray touch must not be able to crash the app.
 */
class InputBridge(private val sink: InputSink) {

    fun handle(event: Map<String, Any?>) {
        when (event["t"]) {
            "move" -> {
                val dx = number(event["dx"])
                val dy = number(event["dy"])
                if (dx != null && dy != null) sink.move(dx, dy)
            }
            "scroll" -> {
                val vertical = number(event["dy"]) ?: return
                sink.scroll(vertical, number(event["dx"]) ?: 0f)
            }
            "click" -> click(event)
            "key" -> key(event)
            "text" -> (event["text"] as? String)?.takeIf { it.isNotEmpty() }?.let { sink.text(it) }
        }
    }

    private fun click(event: Map<String, Any?>) {
        val button = BUTTONS[event["button"]] ?: return
        when (event["action"]) {
            "down" -> sink.button(button, true)
            "up" -> sink.button(button, false)
            else -> {                       // a bare click is a full press and release
                sink.button(button, true)
                sink.button(button, false)
            }
        }
    }

    private fun key(event: Map<String, Any?>) {
        val code = (event["code"] as? String)?.takeIf { it.isNotEmpty() && it.length <= 64 } ?: return
        val mods = (event["mods"] as? List<*>).orEmpty().mapNotNull { MODIFIER_KEYS[it] }.distinct()
        when (event["action"]) {
            "down" -> press(code, mods)
            "up" -> release(code, mods)
            else -> {
                press(code, mods)
                release(code, mods)
            }
        }
    }

    private fun press(code: String, mods: List<String>) {
        for (m in mods) sink.key(m, true)
        sink.key(code, true)
    }

    private fun release(code: String, mods: List<String>) {
        sink.key(code, false)
        for (m in mods.asReversed()) sink.key(m, false)
    }

    private fun number(value: Any?): Float? = (value as? Number)?.toFloat()?.takeIf { it.isFinite() }

    companion object {
        private val BUTTONS = mapOf<Any?, Int>(
            "left" to Mip.BUTTON_LEFT, "middle" to Mip.BUTTON_MIDDLE, "right" to Mip.BUTTON_RIGHT,
            "back" to Mip.BUTTON_BACK, "forward" to Mip.BUTTON_FORWARD,
        )
        private val MODIFIER_KEYS = mapOf<Any?, String>(
            "shift" to "ShiftLeft", "ctrl" to "ControlLeft", "alt" to "AltLeft", "meta" to "MetaLeft",
        )
    }
}
