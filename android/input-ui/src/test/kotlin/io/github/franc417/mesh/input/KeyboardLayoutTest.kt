package io.github.franc417.mesh.input

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

class KeyboardLayoutTest {

    private val default = DefaultLayout.build()
    private fun rowOf(layout: LayoutDef, layer: String, row: Int) = layout.layer(layer)!!.rows[row]
    private fun near(expected: Float, actual: Float, eps: Float = 0.06f) =
        assertTrue("expected $expected but was $actual", abs(expected - actual) <= eps)

    // ---------------------------------------------------------- default layout

    @Test
    fun `every layer is the same total height so the keyboard never resizes`() {
        for (layer in default.layers) {
            near(6.7f, layer.rows.fold(0f) { a, r -> a + r.height }, 0.001f)
        }
    }

    @Test
    fun `default layout uses only valid codes and existing layers`() {
        val names = default.layers.map { it.name }.toSet()
        for (layer in default.layers) for (row in layer.rows) for (key in row.keys) {
            val ok = key.code in PHYSICAL_CODES || key.isSticky || key.isSpacer || key.isFnToggle ||
                key.code == CODE_UNICODE || (key.layerTarget != null && key.layerTarget in names)
            assertTrue("bad key ${key.label}/${key.code} in ${layer.name}", ok)
            if (key.code == CODE_UNICODE) assertNotNull(key.unicode)
        }
    }

    @Test
    fun `letters layer has comma and full stop beside the space bar like Android`() {
        val bottom = rowOf(default, "letters", 6).keys
        val space = bottom.indexOfFirst { it.code == "Space" }
        assertEquals("Comma", bottom[space - 1].code)
        assertEquals("Period", bottom[space + 1].code)
        assertEquals("Enter", bottom.last().code)
        assertEquals("layer:symbols1", bottom.first().code)
    }

    @Test
    fun `symbols page two has the currency and bracket page with angle brackets by the space bar`() {
        val rows = default.layer("symbols2")!!.rows
        assertEquals(listOf("~", "`", "|", "•", "√", "π", "÷", "×", "¶", "∆"), rows[1].keys.map { it.label })
        assertEquals(listOf("£", "¢", "€", "¥", "^", "°", "=", "{", "}", "\\"), rows[2].keys.map { it.label })
        assertEquals(listOf("?123", "%", "©", "®", "™", "✓", "[", "]", "⌫"), rows[3].keys.map { it.label })
        assertEquals(listOf("ABC", "<", "space", ">", "enter"), rows.last().keys.map { it.label })
        assertEquals("20ac", rows[2].keys[2].unicode)
    }

    @Test
    fun `no row has more than ten keys so every key stays comfortably wide`() {
        for (layer in default.layers) for ((i, row) in layer.rows.withIndex()) {
            assertTrue("${layer.name} row $i has ${row.keys.size} keys", row.keys.count { !it.isSpacer } <= 10)
        }
    }

    @Test
    fun `arrow keys are on the 123 layer and not on the letters layer`() {
        val arrows = setOf("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight")
        fun codes(layer: String) = default.layer(layer)!!.rows.flatMap { r -> r.keys.map { it.code } }
        assertTrue(codes("letters").none { it in arrows })
        assertTrue(codes("symbols1").containsAll(arrows))
    }

    @Test
    fun `tab is on the letters page for remote desktops and prtsc is on the letters and 123 pages`() {
        assertTrue(default.layer("letters")!!.rows.flatMap { it.keys }.any { it.code == "Tab" && it.label == "tab" })
        assertTrue(default.layer("letters")!!.rows.flatMap { it.keys }.any { it.code == "PrintScreen" })
        assertTrue(default.layer("symbols1")!!.rows.flatMap { it.keys }.any { it.code == "PrintScreen" })
    }

    @Test
    fun `symbols page one matches Android's 123 page plus the arrow cluster`() {
        val rows = default.layer("symbols1")!!.rows
        fun labels(i: Int) = rows[i].keys.map { it.label }
        assertEquals(listOf("1", "2", "3", "4", "5", "6", "7", "8", "9", "0"), labels(0))
        assertEquals(listOf("@", "#", "$", "%", "&", "-", "+", "(", ")", "/"), labels(1))
        assertEquals(listOf("=\\<", "*", "\"", "'", ":", ";", "!", "?", "\u25B2", "\u232B"), labels(2))
        assertEquals(listOf("esc", "prtsc", "win", "ctrl", "alt", "fn", "\u25C0", "\u25BC", "\u25B6"), labels(3))
        val at = rows[1].keys[0]
        assertEquals("Digit2", at.code)
        assertEquals(listOf("shift"), at.mods) // "@" is Shift+2 on a physical keyboard
        assertEquals(listOf("ABC", ",", "space", ".", "enter"), rows.last().keys.map { it.label })
    }

    @Test
    fun `the up arrow sits directly above the down arrow`() {
        val rows = default.layer("symbols1")!!.rows
        fun centre(row: RowDef, code: String): Float {
            var before = 0f
            for (k in row.keys) {
                if (k.code == code) return (before + k.weight / 2f) / row.totalWeight
                before += k.weight
            }
            error("no $code")
        }
        near(centre(rows[3], "ArrowDown"), centre(rows[2], "ArrowUp"), 0.01f)
    }

    @Test
    fun `fn is not a page of its own`() {
        assertEquals(listOf("letters", "symbols1", "symbols2"), default.layers.map { it.name })
        for (layer in default.layers) {
            assertTrue(layer.rows.flatMap { it.keys }.none { it.layerTarget == "fn" })
        }
    }

    @Test
    fun `esc is above the 1 key and prtsc above the 0 key on the letters layer`() {
        val rows = default.layer("letters")!!.rows
        fun centre(row: RowDef, code: String): Float {
            var before = 0f
            for (k in row.keys) {
                if (k.code == code) return (before + k.weight / 2f) / row.totalWeight
                before += k.weight
            }
            error("no $code")
        }
        assertEquals(listOf("esc", "prtsc"), rows[0].keys.filter { !it.isSpacer }.map { it.label })
        near(centre(rows[1], "Digit1"), centre(rows[0], "Escape"), 0.001f)
        near(centre(rows[1], "Digit0"), centre(rows[0], "PrintScreen"), 0.001f)
        assertEquals(10, rows[1].keys.size)
    }

    @Test
    fun `the modifier row is tab win ctrl alt fn and fn is a toggle`() {
        val row = default.layer("letters")!!.rows[5]
        assertEquals(listOf("tab", "win", "ctrl", "alt", "fn"), row.keys.map { it.label })
        assertTrue(row.keys.last().isFnToggle)
    }

    // ---------------------------------------------------------- fn in place

    private fun variant(code: String, mods: List<String> = emptyList()) = KeyDef("x", code, 1f, mods).fnVariant()

    @Test
    fun `fn turns the digits into F1 to F10`() {
        val digits = default.layer("letters")!!.rows[1].keys
        assertEquals((1..10).map { "F$it" }, digits.map { it.fnVariant()!!.label })
        assertEquals((1..9).map { "F$it" } + "F10", digits.map { it.fnVariant()!!.code })
    }

    @Test
    fun `fn turns esc into F11 and prtsc into F12`() {
        assertEquals("F11", variant("Escape")!!.code)
        assertEquals("F12", variant("PrintScreen")!!.code)
        val top = default.layer("letters")!!.rows[0].keys.filter { !it.isSpacer }
        assertEquals(listOf("F11", "F12"), top.map { it.fnVariant()!!.label })
    }

    @Test
    fun `fn turns the arrows into the navigation keys`() {
        assertEquals("Home", variant("ArrowLeft")!!.label)
        assertEquals("PgUp", variant("ArrowUp")!!.label)
        assertEquals("PgDn", variant("ArrowDown")!!.label)
        assertEquals("End", variant("ArrowRight")!!.label)
        assertEquals("PageUp", variant("ArrowUp")!!.code)   // the wire code is the real key name
    }

    @Test
    fun `fn leaves other keys alone including symbols made with shift`() {
        assertNull(variant("KeyQ"))
        assertNull(variant("Space"))
        assertNull(variant("Digit2", listOf("shift")))      // "@" is Shift+2, not the 2 key
        assertNull(KeyDef("ctrl", "ctrl").fnVariant())
        assertNull(KeyDef("fn", CODE_FN).fnVariant())
        assertNull(KeyDef("", CODE_SPACER).fnVariant())
        assertNull(KeyDef("123", "layer:symbols1").fnVariant())
        assertNull(KeyDef("\u20AC", CODE_UNICODE, 1f, emptyList(), "20ac").fnVariant())
    }

    @Test
    fun `on the 123 page fn changes the digits and the arrow cluster but not the symbols`() {
        val rows = default.layer("symbols1")!!.rows
        assertTrue(rows[0].keys.all { it.fnVariant() != null })                  // digits -> F1..F10
        assertTrue(rows[1].keys.filter { it.mods.isNotEmpty() }.all { it.fnVariant() == null })  // @ # $ % & ( )
        val dock = rows[3].keys
        assertEquals(listOf("F11", "F12"), dock.take(2).map { it.fnVariant()!!.label })
        assertEquals(listOf("Home", "PgDn", "End"), dock.takeLast(3).map { it.fnVariant()!!.label })
        assertEquals("PgUp", rows[2].keys.first { it.code == "ArrowUp" }.fnVariant()!!.label)
    }

    @Test
    fun `a fn variant keeps the width of its key`() {
        assertEquals(1.5f, KeyDef("esc", "Escape", 1.5f).fnVariant()!!.weight, 0.001f)
    }

    // ---------------------------------------------------------- older layout text

    @Test
    fun `old layout text with a separate fn page still parses and its fn key becomes the toggle`() {
        val text = """
            remote-hid keyboard layout v1
            layer letters
            row 1  q|KeyQ  fn|layer:fn|1.5  123|layer:symbols1
            layer fn
            row 1  F1|F1  F2|F2  back|layer:letters
            layer symbols1
            row 1  1|Digit1  ABC|layer:letters
        """.trimIndent()
        val result = LayoutText.parse(text)
        assertEquals(emptyList<String>(), result.errors)
        val layout = result.layout!!
        assertEquals(listOf("letters", "symbols1"), layout.layers.map { it.name })   // the fn page is gone
        val fn = layout.layer("letters")!!.rows[0].keys[1]
        assertEquals(CODE_FN, fn.code)
        assertTrue(fn.isFnToggle)
        assertEquals(1.5f, fn.weight, 0.001f)
    }

    @Test
    fun `the fn toggle round trips through the text`() {
        val result = LayoutText.parse(LayoutText.serialize(default))
        assertEquals(emptyList<String>(), result.errors)
        assertTrue(result.layout!!.layer("letters")!!.rows[5].keys.last().isFnToggle)
    }

    // ---------------------------------------------------------- text round trip

    @Test
    fun `the default layout survives serialize then parse unchanged`() {
        val result = LayoutText.parse(LayoutText.serialize(default))
        assertEquals(emptyList<String>(), result.errors)
        assertEquals(default, result.layout)
    }

    @Test
    fun `awkward labels round trip`() {
        val layout = LayoutDef(
            listOf(
                LayerDef(
                    "letters",
                    listOf(
                        RowDef(
                            listOf(
                                KeyDef("|", "Backslash", 1f, listOf("shift")),
                                KeyDef("\\", "Backslash"),
                                KeyDef("a b", "KeyA", 1.25f),
                                KeyDef(":", "Semicolon", 2f, listOf("shift", "ctrl")),
                                KeyDef("#", "Digit3"),
                                KeyDef("€", CODE_UNICODE, 1f, emptyList(), "20ac"),
                            ),
                            1.3f,
                        ),
                    ),
                ),
            ),
        )
        val result = LayoutText.parse(LayoutText.serialize(layout))
        assertEquals(emptyList<String>(), result.errors)
        assertEquals(layout, result.layout)
    }

    @Test
    fun `comments blank lines and windows line endings are fine`() {
        val text = "remote-hid keyboard layout v1\r\n\r\n# a comment\r\nlayer letters\r\nrow 1  q|KeyQ  w|KeyW|2\r\n"
        val result = LayoutText.parse(text)
        assertEquals(emptyList<String>(), result.errors)
        assertEquals(listOf(1f, 2f), result.layout!!.layers[0].rows[0].keys.map { it.weight })
    }

    // ---------------------------------------------------------- parse errors

    private fun errorsOf(vararg lines: String) =
        LayoutText.parse((listOf(LayoutText.HEADER) + lines).joinToString("\n")).errors

    @Test
    fun `errors point at the line and say what is wrong`() {
        assertTrue(errorsOf("layer a", "row 1 q|KeyQ|nope").single().contains("line 3"))
        assertTrue(errorsOf("layer a", "row 1 q|Nonsense").single().contains("unknown key code"))
        assertTrue(errorsOf("layer a", "row 1 q|KeyQ|0").single().contains("bad width"))
        assertTrue(errorsOf("layer a", "row 1 q|KeyQ|1|hyper").single().contains("unknown modifier"))
        assertTrue(errorsOf("layer a", "row 1 €|unicode").single().contains("hex"))
        assertTrue(errorsOf("layer a", "row 1 €|unicode|1||zz9").single().contains("hex"))
        assertTrue(errorsOf("layer a", "row 1").single().contains("no keys"))
        assertTrue(errorsOf("layer a", "row 9 q|KeyQ").single().contains("height"))
        assertTrue(errorsOf("layer a", "row tall q|KeyQ").single().contains("height"))
        assertTrue(errorsOf("row 1 q|KeyQ").single().contains("after a \"layer"))
        assertTrue(errorsOf("layer a", "row 1 q|KeyQ", "layer a", "row 1 q|KeyQ").single().contains("twice"))
        assertTrue(errorsOf("layer a", "row 1 q|KeyQ|1|shift|20ac|extra").single().contains("too many"))
        assertTrue(errorsOf("layer a", "row 1 q|KeyQ", "banana").single().contains("unknown line"))
        assertTrue(errorsOf("layer a").single().contains("no rows"))
    }

    @Test
    fun `a key pointing at a missing layer is rejected`() {
        val errors = errorsOf("layer a", "row 1 q|KeyQ  go|layer:missing")
        assertEquals(1, errors.size)
        assertTrue(errors[0].contains("missing"))
    }

    @Test
    fun `the header is required and no layout comes back on error`() {
        val result = LayoutText.parse("layer a\nrow 1 q|KeyQ")
        assertNull(result.layout)
        assertTrue(result.errors.first().contains("first line"))
        assertTrue(LayoutText.parse("").errors.isNotEmpty())
        assertTrue(LayoutText.parse("   \n# only a comment").errors.isNotEmpty())
    }

    @Test
    fun `all errors are reported at once not just the first`() {
        val errors = errorsOf("layer a", "row 1 q|Bad1", "row 1 w|Bad2")
        assertEquals(2, errors.size)
    }

    // ---------------------------------------------------------- resize

    private fun simple() = LayoutDef(
        listOf(
            LayerDef(
                "letters",
                listOf(
                    RowDef(listOf(KeyDef("a", "KeyA"), KeyDef("b", "KeyB"), KeyDef("c", "KeyC", 2f), KeyDef("d", "KeyD"))),
                    RowDef(listOf(KeyDef("x", "KeyX"), KeyDef("y", "KeyY"))),
                ),
            ),
        ),
    )

    @Test
    fun `resizing one key keeps the row total and rescales the others in proportion`() {
        val before = simple()
        val after = LayoutEditor.resizeKey(before, "letters", 0, 1, 2.5f)
        val row = rowOf(after, "letters", 0)
        near(5f, row.totalWeight)
        near(2.5f, row.keys[1].weight, 0.001f)
        // a:c:d were 1:2:1 and share the remaining 2.5 in that ratio
        near(row.keys[0].weight * 2, row.keys[2].weight)
        near(row.keys[0].weight, row.keys[3].weight, 0.02f)
    }

    @Test
    fun `resizing does not modify the original layout`() {
        val before = simple()
        LayoutEditor.resizeKey(before, "letters", 0, 1, 3f)
        assertEquals(simple(), before)
    }

    @Test
    fun `a key cannot be grown so far that others fall below the minimum`() {
        val after = LayoutEditor.resizeKey(simple(), "letters", 0, 0, 99f)
        val row = rowOf(after, "letters", 0)
        near(5f, row.totalWeight)
        assertTrue(row.keys.drop(1).all { it.weight >= MIN_KEY_WEIGHT - 0.001f })
        near(5f - MIN_KEY_WEIGHT * 3, row.keys[0].weight, 0.02f)
    }

    @Test
    fun `a key cannot be shrunk below the minimum`() {
        val after = LayoutEditor.resizeKey(simple(), "letters", 0, 2, 0.01f)
        near(MIN_KEY_WEIGHT, rowOf(after, "letters", 0).keys[2].weight, 0.001f)
        near(5f, rowOf(after, "letters", 0).totalWeight)
    }

    @Test
    fun `resizing a row that is already tight pins the narrow keys at the minimum`() {
        val tight = LayoutDef(
            listOf(LayerDef("letters", listOf(RowDef(listOf(KeyDef("a", "KeyA", 0.5f), KeyDef("b", "KeyB", 0.5f), KeyDef("c", "KeyC", 3f)))))),
        )
        val after = LayoutEditor.resizeKey(tight, "letters", 0, 2, 1.0f)
        val row = rowOf(after, "letters", 0)
        near(4f, row.totalWeight)
        assertTrue(row.keys.all { it.weight >= MIN_KEY_WEIGHT - 0.001f })
    }

    @Test
    fun `resize ignores bad input instead of crashing`() {
        val l = simple()
        assertEquals(l, LayoutEditor.resizeKey(l, "nope", 0, 0, 2f))
        assertEquals(l, LayoutEditor.resizeKey(l, "letters", 9, 0, 2f))
        assertEquals(l, LayoutEditor.resizeKey(l, "letters", 0, 9, 2f))
        val single = LayoutDef(listOf(LayerDef("letters", listOf(RowDef(listOf(KeyDef("a", "KeyA")))))))
        assertEquals(single, LayoutEditor.resizeKey(single, "letters", 0, 0, 3f))
    }

    @Test
    fun `repeated resizes do not drift the row total`() {
        var l = simple()
        repeat(60) { i -> l = LayoutEditor.resizeKey(l, "letters", 0, i % 4, 0.7f + (i % 5) * 0.4f) }
        near(5f, rowOf(l, "letters", 0).totalWeight, 0.25f)
        assertTrue(rowOf(l, "letters", 0).keys.all { it.weight >= MIN_KEY_WEIGHT - 0.001f })
    }

    // ---------------------------------------------------------- move

    @Test
    fun `moving a key within a row reorders and keeps every width`() {
        val after = LayoutEditor.moveKey(simple(), "letters", 0, 0, 2)
        assertEquals(listOf("b", "c", "a", "d"), rowOf(after, "letters", 0).keys.map { it.label })
        assertEquals(simple().layers[0].rows[0].totalWeight, rowOf(after, "letters", 0).totalWeight, 0.001f)
        assertEquals(2f, rowOf(after, "letters", 0).keys[1].weight, 0.001f)
    }

    @Test
    fun `moving a key to a position it already has or out of range changes nothing`() {
        val l = simple()
        assertEquals(l, LayoutEditor.moveKey(l, "letters", 0, 1, 1))
        assertEquals(l, LayoutEditor.moveKey(l, "letters", 0, 1, 9))
        assertEquals(l, LayoutEditor.moveKey(l, "letters", 0, -1, 0))
    }

    @Test
    fun `moving a key to another row keeps all keys and fills both rows`() {
        val after = LayoutEditor.moveKeyToRow(simple(), "letters", 0, 3, 1, 1)
        assertEquals(listOf("a", "b", "c"), rowOf(after, "letters", 0).keys.map { it.label })
        assertEquals(listOf("x", "d", "y"), rowOf(after, "letters", 1).keys.map { it.label })
        assertEquals(6, after.layers[0].rows.sumOf { it.keys.size })
    }

    @Test
    fun `the last key in a row cannot be moved out`() {
        val l = LayoutDef(
            listOf(LayerDef("letters", listOf(RowDef(listOf(KeyDef("a", "KeyA"))), RowDef(listOf(KeyDef("b", "KeyB")))))),
        )
        assertEquals(l, LayoutEditor.moveKeyToRow(l, "letters", 0, 0, 1, 0))
    }

    @Test
    fun `row height is clamped`() {
        assertEquals(MAX_ROW_HEIGHT, rowOf(LayoutEditor.setRowHeight(simple(), "letters", 0, 99f), "letters", 0).height, 0.001f)
        assertEquals(MIN_ROW_HEIGHT, rowOf(LayoutEditor.setRowHeight(simple(), "letters", 0, 0f), "letters", 0).height, 0.001f)
        assertEquals(1.4f, rowOf(LayoutEditor.setRowHeight(simple(), "letters", 1, 1.4f), "letters", 1).height, 0.001f)
        assertFalse(LayoutEditor.setRowHeight(simple(), "letters", 5, 2f) != simple())
    }

    @Test
    fun `an edited layout still round trips through text`() {
        var l = DefaultLayout.build()
        l = LayoutEditor.resizeKey(l, "letters", 1, 4, 1.8f)
        l = LayoutEditor.moveKey(l, "letters", 2, 1, 5)
        l = LayoutEditor.setRowHeight(l, "letters", 3, 1.35f)
        val parsed = LayoutText.parse(LayoutText.serialize(l))
        assertEquals(emptyList<String>(), parsed.errors)
        assertEquals(l, parsed.layout)
    }
}
