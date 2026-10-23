package io.github.franc417.mesh

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Toast
import io.github.franc417.mesh.core.HostAddress
import io.github.franc417.mesh.core.PairInfo
import java.util.UUID

/** Your computers, and a way to add one. Tap a computer to control it. */
class MainActivity : Activity() {

    private lateinit var store: ComputerStore
    private lateinit var listBox: LinearLayout
    private lateinit var nameInput: EditText
    private lateinit var hostInput: EditText
    private lateinit var pairInput: EditText

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = ComputerStore(this)
        setContentView(build())
    }

    override fun onResume() {
        super.onResume()
        renderList()
    }

    private fun build(): View {
        val pad = Ui.dp(this, 20)
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(ImageView(this).apply {
            setImageResource(R.drawable.ic_launcher_foreground)
            layoutParams = LinearLayout.LayoutParams(Ui.dp(this@MainActivity, 72), Ui.dp(this@MainActivity, 72))
        })
        val titles = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        titles.addView(Ui.text(this, "Mesh", 28f, Ui.TEXT, bold = true))
        titles.addView(Ui.text(this, "Your computers", 14f, Ui.MUTED))
        header.addView(titles)
        column.addView(header)
        column.addView(Ui.gap(this, 20))

        listBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        column.addView(listBox)

        column.addView(Ui.gap(this, 28))
        column.addView(Ui.text(this, "Add a computer", 18f, Ui.TEXT, bold = true))
        column.addView(Ui.gap(this, 4))
        column.addView(Ui.text(this, "On the computer run:  python3 mesh_input.py pair-info  and paste what it prints.", 13f, Ui.MUTED))
        column.addView(Ui.gap(this, 12))

        nameInput = field("Name (optional)")
        hostInput = field("Address (optional: the pairing info already lists them)", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        pairInput = field("Pairing info", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS).apply {
            minLines = 3
            gravity = Gravity.TOP
        }
        for (f in listOf(nameInput, hostInput, pairInput)) {
            column.addView(f)
            column.addView(Ui.gap(this, 10))
        }
        val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        buttons.addView(Ui.button(this, "Paste from clipboard") { pasteFromClipboard() }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        buttons.addView(android.view.View(this), LinearLayout.LayoutParams(Ui.dp(this, 10), 1))
        buttons.addView(Ui.button(this, "Save") { save() }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        column.addView(buttons)

        return ScrollView(this).apply {
            isFillViewport = true
            addView(column)
        }
    }

    private fun field(hint: String, type: Int = InputType.TYPE_CLASS_TEXT) = EditText(this).apply {
        this.hint = hint
        inputType = type
        setTextColor(Ui.TEXT)
        setHintTextColor(Ui.MUTED)
        textSize = 15f
        background = Ui.box(this@MainActivity)
        val h = Ui.dp(this@MainActivity, 14)
        setPadding(h, Ui.dp(this@MainActivity, 12), h, Ui.dp(this@MainActivity, 12))
    }

    private fun pasteFromClipboard() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = clipboard.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString()
        if (text.isNullOrBlank()) {
            toast("Nothing on the clipboard")
        } else {
            pairInput.setText(text)
        }
    }

    private fun save() {
        val typed = hostInput.text.toString().trim()
        val typedHost = if (typed.isEmpty()) null else try {
            HostAddress.parse(typed)
        } catch (e: HostAddress.Invalid) {
            toast(e.message ?: "That address cannot work")
            return
        }
        val info = try {
            PairInfo.parse(pairInput.text.toString())
        } catch (e: PairInfo.ParseException) {
            toast(e.message ?: "That is not valid pairing info")
            return
        }
        val hosts = (listOfNotNull(typedHost) + info.addresses).distinct()
        if (hosts.isEmpty()) {
            toast("The pairing info lists no addresses, so type the computer's address in the box above")
            return
        }
        val name = nameInput.text.toString().trim().ifEmpty { info.hostName.ifEmpty { hosts.first() } }
        store.add(Computer(UUID.randomUUID().toString(), name, hosts, info.port, info.fingerprint, info.tokenHex))
        for (f in listOf(nameInput, hostInput, pairInput)) f.setText("")
        renderList()
        toast("Saved $name (${hosts.size} address${if (hosts.size == 1) "" else "es"})")
    }

    private fun renderList() {
        listBox.removeAllViews()
        val computers = store.all()
        if (computers.isEmpty()) {
            listBox.addView(Ui.text(this, "No computers yet. Add one below.", 14f, Ui.MUTED))
            return
        }
        for (c in computers) {
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                background = Ui.box(this@MainActivity)
                val p = Ui.dp(this@MainActivity, 16)
                setPadding(p, p, p, p)
                addView(Ui.text(this@MainActivity, c.name, 17f, Ui.TEXT, bold = true))
                val where = when {
                    c.hosts.isEmpty() -> "no usable address saved: remove it and add it again"
                    c.hosts.size == 1 -> "${c.hosts[0]}:${c.port}"
                    else -> "${c.hosts[0]}:${c.port}  (+${c.hosts.size - 1} more)"
                }
                addView(Ui.text(this@MainActivity, where, 13f, if (c.hosts.isEmpty()) Ui.ERROR else Ui.MUTED))
                setOnClickListener { startActivity(Intent(this@MainActivity, ControlActivity::class.java).putExtra(ControlActivity.EXTRA_ID, c.id)) }
                setOnLongClickListener {
                    confirmRemove(c)
                    true
                }
            }
            listBox.addView(card)
            listBox.addView(Ui.gap(this, 10))
        }
    }

    private fun confirmRemove(c: Computer) {
        AlertDialog.Builder(this)
            .setTitle("Remove ${c.name}?")
            .setMessage("This phone will forget the pairing. The computer keeps its own pairing token until you reset it there.")
            .setPositiveButton("Remove") { _, _ ->
                store.remove(c.id)
                renderList()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()
}
