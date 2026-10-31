package io.github.franc417.mesh

import android.app.Activity
import android.app.AlertDialog
import android.app.Application
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Typeface
import android.os.Build
import android.util.Log
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import io.github.franc417.mesh.core.CrashReport
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * When the app crashes, the stack trace is saved to a small file; the next time the first screen opens it offers the text with a
 * Copy button. Without this a crash on a phone leaves nothing to paste back, which makes it impossible to fix from here.
 */
object CrashReporter {
    private const val FILE = "last-crash.txt"
    private var dialog: AlertDialog? = null

    fun install(app: Application) {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            try {
                File(app.filesDir, FILE).writeText(describe(app, thread, error))
            } catch (ignored: Throwable) {
                // Recording must never get in the way of the real crash being reported to the system as usual.
            }
            previous?.uncaughtException(thread, error)
        }
    }

    @Suppress("DEPRECATION")
    private fun describe(context: Context, thread: Thread, error: Throwable): String {
        val installed = try {
            context.packageManager.getPackageInfo(context.packageName, 0).let { "${it.versionName}, installed ${stamp(Date(it.lastUpdateTime))}" }
        } catch (ignored: Throwable) {
            "unknown"
        }
        return CrashReport.format(
            timeText = stamp(Date()),
            appVersion = installed,
            android = "${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})",
            device = "${Build.MANUFACTURER} ${Build.MODEL}",
            thread = thread.name,
            stackTrace = Log.getStackTraceString(error),
        )
    }

    private fun stamp(date: Date): String = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(date)

    private fun pending(context: Context): String? {
        val file = File(context.filesDir, FILE)
        return if (file.exists()) file.readText().takeIf { it.isNotBlank() } else null
    }

    private fun clear(context: Context) {
        File(context.filesDir, FILE).delete()
    }

    /** Shows the saved report, if there is one. Closing it with the back button keeps it for next time. */
    fun showIfPending(activity: Activity) {
        val report = pending(activity) ?: return
        if (dialog?.isShowing == true) return
        val body = TextView(activity).apply {
            text = report
            textSize = 11f
            typeface = Typeface.MONOSPACE
            setTextIsSelectable(true)
            val pad = Ui.dp(activity, 16)
            setPadding(pad, pad, pad, pad)
        }
        dialog = AlertDialog.Builder(activity)
            .setTitle("Mesh crashed last time")
            .setView(ScrollView(activity).apply { addView(body) })
            .setPositiveButton("Copy and close") { _, _ ->
                val clipboard = activity.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                clipboard.setPrimaryClip(ClipData.newPlainText("Mesh crash report", report))
                Toast.makeText(activity, "Copied. Paste it into the chat.", Toast.LENGTH_SHORT).show()
                clear(activity)
            }
            .setNegativeButton("Close") { _, _ -> clear(activity) }
            .show()
    }
}
