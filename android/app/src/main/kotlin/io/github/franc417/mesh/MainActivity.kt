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

/**
 * Your computers. Each card opens the desktop view, the input-only screen, or the edit screen; "Add desktop" adds one.
 * Adding and editing share one form screen (ComputerFormActivity).
 */
class MainActivity : Activity() {

    private lateinit var store: ComputerStore
    private lateinit var listBox: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = ComputerStore(this)
        setContentView(build())
    }

    override fun onResume() {
        super.onResume()
        renderList() // also picks up what the form screen just added or changed
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
        column.addView(Ui.gap(this, 16))

        column.addView(Ui.button(this, "+  Add desktop") {
            startActivity(Intent(this, ComputerFormActivity::class.java))
        })
        column.addView(Ui.gap(this, 16))

        listBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        column.addView(listBox)

        return ScrollView(this).apply {
            isFillViewport = true
            addView(column)
        }
    }

    private fun renderList() {
        listBox.removeAllViews()
        val computers = store.all()
        if (computers.isEmpty()) {
            listBox.addView(Ui.text(this, "No computers yet. Tap Add desktop.", 14f, Ui.MUTED))
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
