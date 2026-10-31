package io.github.franc417.mesh.core

/**
 * The text of a crash report: a few lines about the phone and the app, then the stack trace, short enough to paste into a
 * chat message. Kept free of Android classes so it is tested on an ordinary JVM; the app fills in the facts.
 */
object CrashReport {
    const val MAX_TRACE_CHARS = 6000

    fun format(timeText: String, appVersion: String, android: String, device: String, thread: String, stackTrace: String): String =
        buildString {
            appendLine("Mesh crash report")
            appendLine("time: $timeText")
            appendLine("app: $appVersion")
            appendLine("android: $android")
            appendLine("device: $device")
            appendLine("thread: $thread")
            appendLine()
            append(shorten(stackTrace.trim(), MAX_TRACE_CHARS))
        }

    /**
     * A long trace keeps its beginning (what crashed and where) and its END (the "Caused by:" root cause is printed last),
     * and drops the middle, which is mostly the same framework frames repeated.
     */
    fun shorten(text: String, max: Int): String {
        if (text.length <= max) return text
        val head = max * 2 / 5
        val tail = max - head
        val dropped = text.length - head - tail
        return text.take(head) + "\n... ($dropped characters cut from the middle) ...\n" + text.takeLast(tail)
    }
}
