package io.github.franc417.mesh.input

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Drives the default keyboard like a user: taps keys in sequence through resolveTap and checks what would be sent. */
class KeyTapTest {

    private val layout = DefaultLayout.build()

    /** The state the view keeps: current layer, whether fn is on, armed sticky modifiers, and what was sent. */
    private inner class Session {
        var layer = "letters"
        var fn = false
        val mods = linkedSetOf<String>()
        val sent = mutableListOf<Pair<String, List<String>>>()

        fun key(label: String): KeyDef =
            layout.layer(layer)!!.rows.flatMap { it.keys }.first { it.label == label }

        fun shownLabel(label: String) = displayedKey(key(label), fn, false).label

        fun tap(label: String) {
            when (val a = resolveTap(key(label), fn, false, layer, layout.defaultLayerName)) {
                is TapAction.ToggleFn -> fn = !fn
                is TapAction.SwitchLayer -> layer = a.name
                is TapAction.ToggleModifier -> if (!mods.remove(a.mod)) mods.add(a.mod)
                is TapAction.SendUnicode -> sent.add("U+${a.hex}" to emptyList())
                is TapAction.SendKey -> {
                    sent.add(a.code to (mods + a.mods).distinct())
                    mods.clear()
                }
            }
        }
    }

    private fun session(block: Session.() -> Unit) = Session().apply(block)

    @Test
    fun `a digit sends the digit with fn off and the F key with fn on`() {
        val s = session {
            tap("5")
            tap("fn"); tap("5")
            tap("fn"); tap("5")
        }
        assertEquals(listOf("Digit5", "F5", "Digit5"), s.sent.map { it.first })
    }

    @Test
    fun `tab sends Tab whether or not fn is on`() {
        val off = session { tap("tab") }
        assertEquals(listOf("Tab"), off.sent.map { it.first })
        val on = session { tap("fn"); tap("tab") }
        assertEquals(listOf("Tab"), on.sent.map { it.first })      // fn only remaps the keys it has a meaning for
    }

    @Test
    fun `alt then tab sends Alt plus Tab once`() {
        val s = session { tap("alt"); tap("tab") }
        assertEquals(listOf("Tab" to listOf("alt")), s.sent)
    }

    @Test
    fun `fn lights the whole number row as F1 to F10`() {
        val s = session { tap("fn") }
        assertEquals((1..10).map { "F$it" }, listOf("1", "2", "3", "4", "5", "6", "7", "8", "9", "0").map { s.shownLabel(it) })
        s.apply { for (d in listOf("1", "2", "3", "4", "5", "6", "7", "8", "9", "0")) tap(d) }
        assertEquals((1..10).map { "F$it" }, s.sent.map { it.first })
    }

    @Test
    fun `esc and prtsc become F11 and F12 with fn and are esc and prtsc without it`() {
        val off = session { tap("esc"); tap("prtsc") }
        assertEquals(listOf("Escape", "PrintScreen"), off.sent.map { it.first })
        val on = session { tap("fn"); tap("esc"); tap("prtsc") }
        assertEquals(listOf("F11", "F12"), on.sent.map { it.first })
        assertEquals(listOf("F11", "F12"), on.let { listOf(it.shownLabel("esc"), it.shownLabel("prtsc")) })
    }

    @Test
    fun `fn stays on across pages and changes the digits and arrows on the 123 page`() {
        val s = session {
            tap("fn")
            tap("123")                       // fn is not a page: it carries over to the symbols page
        }
        assertEquals("symbols1", s.layer)
        assertTrue(s.fn)
        s.apply {
            tap("5"); tap("\u25B2"); tap("\u25BC"); tap("\u25C0"); tap("\u25B6")
        }
        assertEquals(listOf("F5", "PageUp", "PageDown", "Home", "End"), s.sent.map { it.first })
    }

    @Test
    fun `arrows are arrows with fn off`() {
        val s = session { tap("123"); tap("\u25B2"); tap("\u25BC"); tap("\u25C0"); tap("\u25B6") }
        assertEquals(listOf("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"), s.sent.map { it.first })
    }

    @Test
    fun `symbols made with shift are not turned into F keys by fn`() {
        val s = session { tap("fn"); tap("123"); tap("@"); tap("#"); tap("(") }
        assertEquals(
            listOf("Digit2" to listOf("shift"), "Digit3" to listOf("shift"), "Digit9" to listOf("shift")),
            s.sent,
        )
    }

    @Test
    fun `ctrl plus fn plus a digit sends ctrl F3 once and then the modifier is spent`() {
        val s = session { tap("ctrl"); tap("fn"); tap("3"); tap("3") }
        assertEquals(listOf("F3" to listOf("ctrl"), "F3" to emptyList()), s.sent)
        assertTrue(s.fn)                          // fn is not one-shot, unlike ctrl
    }

    @Test
    fun `fn is pressed again to go back to normal keys from any page`() {
        val s = session { tap("fn"); tap("123"); tap("fn"); tap("5") }
        assertEquals("Digit5", s.sent.single().first)
        assertTrue(!s.fn)
    }

    @Test
    fun `shift then a letter sends shift plus the letter and clears`() {
        val s = session { tap("\u21E7"); tap("a"); tap("a") }
        assertEquals(listOf("KeyA" to listOf("shift"), "KeyA" to emptyList()), s.sent)
    }

    @Test
    fun `the 123 and ABC keys move between pages and the =lt key opens the second symbols page`() {
        val s = session { tap("123") }
        assertEquals("symbols1", s.layer)
        s.tap("=\\<")
        assertEquals("symbols2", s.layer)
        s.tap("?123")
        assertEquals("symbols1", s.layer)
        s.tap("ABC")
        assertEquals("letters", s.layer)
    }

    @Test
    fun `a page toggle pressed on its own page returns to the first page`() {
        val key = KeyDef("back", "layer:symbols1")
        assertEquals(TapAction.SwitchLayer("letters"), resolveTap(key, false, false, "symbols1", "letters"))
        assertEquals(TapAction.SwitchLayer("symbols1"), resolveTap(key, false, false, "letters", "letters"))
    }

    @Test
    fun `characters without a physical key are sent as unicode even with fn on`() {
        val s = session { tap("fn"); tap("123"); tap("=\\<"); tap("\u20AC") }
        assertEquals(listOf("U+20ac"), s.sent.map { it.first })
    }

    @Test
    fun `comma and full stop send their keys and with shift on the second page send angle brackets`() {
        val s = session { tap(","); tap("."); tap("123"); tap("=\\<"); tap("<"); tap(">") }
        assertEquals(
            listOf("Comma" to emptyList(), "Period" to emptyList(), "Comma" to listOf("shift"), "Period" to listOf("shift")),
            s.sent,
        )
    }

    @Test
    fun `editing always shows the real keys even if fn was on`() {
        val digit = layout.layer("letters")!!.rows[1].keys[0]
        assertEquals("F1", displayedKey(digit, true, false).label)
        assertEquals("1", displayedKey(digit, true, true).label)
    }

    @Test
    fun `every key on every page with fn on or off resolves to something the desktop understands`() {
        val validMods = setOf("shift", "ctrl", "alt", "meta")
        for (layer in layout.layers) for (row in layer.rows) for (key in row.keys) {
            if (key.isSpacer) continue
            for (fn in listOf(false, true)) {
                when (val a = resolveTap(key, fn, false, layer.name, layout.defaultLayerName)) {
                    is TapAction.SendKey -> {
                        assertTrue("${layer.name}/${key.label} fn=$fn sends unknown code ${a.code}", a.code in PHYSICAL_CODES)
                        assertTrue("${layer.name}/${key.label} fn=$fn has odd mods ${a.mods}", validMods.containsAll(a.mods))
                    }
                    is TapAction.SwitchLayer -> assertTrue(layout.layer(a.name) != null)
                    is TapAction.SendUnicode -> assertTrue(a.hex.isNotEmpty())
                    is TapAction.ToggleFn, is TapAction.ToggleModifier -> {}
                }
            }
        }
    }
}
