package io.github.franc417.mesh

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Toast
import io.github.franc417.mesh.core.ComputerForm
import io.github.franc417.mesh.core.PairInfo
import java.util.UUID

/**
 * Add a desktop, or edit one that is already saved. Without an id in the intent it adds; with one it edits that computer:
 * its name, the addresses to reach it at, the port, and (only if the computer's agent was reinstalled or reset) new
 * pairing info. The rules for what is accepted live in mesh-core's ComputerForm so they are unit tested.
 */
class ComputerFormActivity : Activity() {

    private lateinit var store: ComputerStore
    private var existing: Computer? = null
    private lateinit var nameInput: EditText
    private lateinit var addressInput: EditText
    private var portInput: EditText? = null
    private lateinit var pairInput: EditText

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = ComputerStore(this)
        val id = intent.getStringExtra(EXTRA_ID)
        existing = store.find(id)
        if (id != null && existing == null) {
            finish() // asked to edit a computer that no longer exists
            return
        }
        setContentView(build())
    }

    private fun build(): View {
        val editing = existing
        val pad = Ui.dp(this, 20)
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }

        column.addView(Ui.text(this, if (editing == null) "Add desktop" else "Edit ${editing.name}", 24f, Ui.TEXT, bold = true))
        column.addView(Ui.gap(this, 6))
        column.addView(Ui.text(this, if (editing == null) {
            "On the computer run:  python3 mesh_input.py pair-info  and paste what it prints below."
        } else {
            "Change what this phone knows about the computer. Nothing changes on the computer itself."
        }, 13f, Ui.MUTED))
        column.addView(Ui.gap(this, 18))

        column.addView(label("Name"))
        nameInput = field(if (editing == null) "Optional: the pairing info already has one" else "Name", InputType.TYPE_CLASS_TEXT)
        editing?.let { nameInput.setText(it.name) }
        column.addView(nameInput)
        column.addView(Ui.gap(this, 14))

        column.addView(label("Addresses"))
        column.addView(Ui.text(this, "One per line. All are tried at once and the first that answers is used, so you can list the home Wi-Fi address and the Tailscale address together.", 12f, Ui.MUTED))
        column.addView(Ui.gap(this, 6))
        addressInput = field(
            if (editing == null) "Optional: the pairing info already lists them" else "For example 192.168.1.20",
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_VARIATION_URI,
        ).apply {
            minLines = 2
            gravity = Gravity.TOP
        }
        editing?.let { addressInput.setText(ComputerForm.formatAddresses(it.hosts)) }
        column.addView(addressInput)
        column.addView(Ui.gap(this, 14))

        if (editing != null) {
            column.addView(label("Port of the Mesh agent"))
            portInput = field("Port", InputType.TYPE_CLASS_NUMBER).also {
                it.setText(editing.port.toString())
                column.addView(it)
            }
            column.addView(Ui.gap(this, 14))
        }

        column.addView(label(if (editing == null) "Pairing info" else "New pairing info (optional)"))
        if (editing != null) {
            column.addView(Ui.text(this, "Only needed if the agent on the computer was reinstalled or its pairing was reset. It replaces the saved pairing, and its port wins over the one above.", 12f, Ui.MUTED))
            column.addView(Ui.gap(this, 6))
        }
        pairInput = field("Paste here", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS).apply {
            minLines = 3
            gravity = Gravity.TOP
        }
        column.addView(pairInput)
        column.addView(Ui.gap(this, 10))
        column.addView(Ui.button(this, "Paste from clipboard") { pasteFromClipboard() })
        column.addView(Ui.gap(this, 22))

        val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        buttons.addView(Ui.button(this, "Cancel") { finish() }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        buttons.addView(View(this), LinearLayout.LayoutParams(Ui.dp(this, 10), 1))
        buttons.addView(Ui.button(this, if (editing == null) "Add" else "Save") { save() }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 2f))
        column.addView(buttons)

        if (editing != null) {
            column.addView(Ui.gap(this, 22))
            column.addView(Ui.button(this, "Remove this computer") { confirmRemove(editing) }.apply { setTextColor(Ui.ERROR) })
        }

        return ScrollView(this).apply {
            isFillViewport = true
            addView(column)
        }
    }

    private fun label(text: String) = Ui.text(this, text, 14f, Ui.TEXT, bold = true).apply {
        setPadding(0, 0, 0, Ui.dp(this@ComputerFormActivity, 6))
    }

    private fun field(hint: String, type: Int) = EditText(this).apply {
        this.hint = hint
        inputType = type
        setTextColor(Ui.TEXT)
        setHintTextColor(Ui.MUTED)
        textSize = 15f
        background = Ui.box(this@ComputerFormActivity)
        val h = Ui.dp(this@ComputerFormActivity, 14)
        setPadding(h, Ui.dp(this@ComputerFormActivity, 12), h, Ui.dp(this@ComputerFormActivity, 12))
    }

    private fun pasteFromClipboard() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = clipboard.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString()
        if (text.isNullOrBlank()) toast("Nothing on the clipboard") else pairInput.setText(text)
    }

    private fun save() {
        try {
            val editing = existing
            if (editing == null) add() else edit(editing)
        } catch (e: ComputerForm.Invalid) {
            toast(e.message ?: "That cannot work")
        } catch (e: PairInfo.ParseException) {
            toast(e.message ?: "That is not valid pairing info")
        }
    }

    private fun add() {
        val info = PairInfo.parse(pairInput.text.toString())
        val typed = addressInput.text.toString()
        val typedHosts = if (typed.isBlank()) emptyList() else ComputerForm.parseAddresses(typed)
        val hosts = (typedHosts + info.addresses).distinct()
        if (hosts.isEmpty()) {
            toast("The pairing info lists no addresses, so type the computer's address in the Addresses box")
            return
        }
        val name = ComputerForm.parseName(nameInput.text.toString(), info.hostName.ifEmpty { hosts.first() })
        store.add(Computer(UUID.randomUUID().toString(), name, hosts, info.port, info.fingerprint, info.tokenHex))
        toast("Added $name (${hosts.size} address${if (hosts.size == 1) "" else "es"})")
        finish()
    }

    private fun edit(editing: Computer) {
        val hosts = ComputerForm.parseAddresses(addressInput.text.toString())
        val name = ComputerForm.parseName(nameInput.text.toString(), editing.name)
        var port = ComputerForm.parsePort(portInput?.text?.toString().orEmpty(), editing.port)
        var fingerprint = editing.fingerprint
        var tokenHex = editing.tokenHex
        val newPairing = pairInput.text.toString()
        if (newPairing.isNotBlank()) {
            val info = PairInfo.parse(newPairing)
            port = info.port
            fingerprint = info.fingerprint
            tokenHex = info.tokenHex
        }
        store.update(editing.copy(name = name, hosts = hosts, port = port, fingerprint = fingerprint, tokenHex = tokenHex))
        toast("Saved $name")
        finish()
    }

    private fun confirmRemove(c: Computer) {
        AlertDialog.Builder(this)
            .setTitle("Remove ${c.name}?")
            .setMessage("This phone will forget the pairing. The computer keeps its own pairing token until you reset it there.")
            .setPositiveButton("Remove") { _, _ ->
                store.remove(c.id)
                finish()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    companion object {
        /** Present: edit that computer. Absent: add a new one. */
        const val EXTRA_ID = "computer_id"
    }
}
