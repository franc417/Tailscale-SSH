package io.github.franc417.mesh

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import io.github.franc417.mesh.core.AudioChoice

/**
 * Your computers. Each card opens the desktop view, the input-only screen, or the edit screen; "Add desktop" adds one.
 * Adding and editing share one form screen (ComputerFormActivity).
 */
class MainActivity : Activity() {

    private lateinit var store: ComputerStore
    private lateinit var listBox: LinearLayout
    private lateinit var audioButton: TextView
    private lateinit var audioHelp: TextView

    /** The computer whose card is highlighted: the sound button at the top belongs to it. */
    private var selectedId: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = ComputerStore(this)
        setContentView(build())
    }

    override fun onResume() {
        super.onResume()
        renderList() // also picks up what the form screen just added or changed
        CrashReporter.showIfPending(this) // the last crash, if there was one, with a Copy button
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
        titles.addView(Ui.text(this, "Build " + getString(R.string.mesh_build), 11f, Ui.MUTED))
        header.addView(titles)
        column.addView(header)
        column.addView(Ui.gap(this, 16))

        column.addView(Ui.button(this, "+  Add desktop") {
            startActivity(Intent(this, ComputerFormActivity::class.java))
        })
        column.addView(Ui.gap(this, 16))
        // Where the sound comes out for the highlighted computer, chosen before opening its desktop (the computer by default).
        audioHelp = Ui.text(this, "", 12f, Ui.MUTED)
        audioButton = Ui.button(this, "") {
            val id = selectedId ?: return@button
            AudioPrefs.setOnComputer(this, id, !AudioPrefs.onComputer(this, id))
            refreshAudio()
        }
        column.addView(audioButton)
        column.addView(Ui.gap(this, 6))
        column.addView(audioHelp)
        column.addView(Ui.gap(this, 16))

        listBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        column.addView(listBox)

        return ScrollView(this).apply {
            isFillViewport = true
            addView(column)
        }
    }

    /** Shows the sound choice of the highlighted computer (hidden while there is none). */
    private fun refreshAudio() {
        val computer = store.all().firstOrNull { it.id == selectedId }
        if (computer == null) {
            audioButton.visibility = View.GONE
            audioHelp.visibility = View.GONE
            return
        }
        val onComputer = AudioPrefs.onComputer(this, computer.id)
        audioButton.visibility = View.VISIBLE
        audioHelp.visibility = View.VISIBLE
        audioButton.text = AudioChoice.buttonLabel(computer.name, onComputer)
        audioHelp.text = AudioChoice.explanation(onComputer)
    }

    private fun select(id: String) {
        selectedId = id
        AudioPrefs.setSelectedId(this, id)
        renderList()
    }

    private fun renderList() {
        listBox.removeAllViews()
        val computers = store.all()
        selectedId = AudioChoice.pickSelected(computers.map { it.id }, selectedId ?: AudioPrefs.selectedId(this))
        refreshAudio()
        if (computers.isEmpty()) {
            listBox.addView(Ui.text(this, "No computers yet. Tap Add desktop.", 14f, Ui.MUTED))
            return
        }
        for (c in computers) {
            // With two or more computers the tapped one is highlighted (white border); the sound button at the top is its own.
            val highlighted = computers.size > 1 && c.id == selectedId
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                background = if (highlighted) Ui.box(this@MainActivity, fill = Ui.SELECTED, stroke = Ui.TEXT) else Ui.box(this@MainActivity)
                setOnClickListener { select(c.id) }
                val p = Ui.dp(this@MainActivity, 16)
                setPadding(p, p, p, p)
                addView(Ui.text(this@MainActivity, c.name, 17f, Ui.TEXT, bold = true))
                val where = when {
                    c.hosts.isEmpty() -> "no usable address saved: tap Edit and add one"
                    c.hosts.size == 1 -> "${c.hosts[0]}:${c.port}"
                    else -> "${c.hosts[0]}:${c.port}  (+${c.hosts.size - 1} more)"
                }
                addView(Ui.text(this@MainActivity, where, 13f, if (c.hosts.isEmpty()) Ui.ERROR else Ui.MUTED))
                addView(Ui.gap(this@MainActivity, 12))
                addView(Ui.button(this@MainActivity, "Open desktop") {
                    startActivity(Intent(this@MainActivity, StreamActivity::class.java).putExtra(StreamActivity.EXTRA_ID, c.id))
                })
                addView(Ui.gap(this@MainActivity, 8))
                val more = LinearLayout(this@MainActivity).apply { orientation = LinearLayout.HORIZONTAL }
                more.addView(Ui.button(this@MainActivity, "Input only") {
                    startActivity(Intent(this@MainActivity, ControlActivity::class.java).putExtra(ControlActivity.EXTRA_ID, c.id))
                }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                more.addView(View(this@MainActivity), LinearLayout.LayoutParams(Ui.dp(this@MainActivity, 10), 1))
                more.addView(Ui.button(this@MainActivity, "Edit") {
                    startActivity(Intent(this@MainActivity, ComputerFormActivity::class.java).putExtra(ComputerFormActivity.EXTRA_ID, c.id))
                }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                addView(more)
            }
            listBox.addView(card)
            listBox.addView(Ui.gap(this, 10))
        }
    }
}
