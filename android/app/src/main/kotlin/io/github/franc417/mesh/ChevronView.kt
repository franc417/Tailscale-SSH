package io.github.franc417.mesh

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.view.View

/** A chevron drawn with a path: pointing down ("open the controls") or up ("tuck them away"). */
class ChevronView(context: Context) : View(context) {

    var pointingUp: Boolean = false
        set(value) {
            field = value
            invalidate()
        }

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Ui.TEXT
        style = Paint.Style.STROKE
        strokeWidth = Ui.dp(context, 2).toFloat() * 1.1f
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    private val path = Path()

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val dx = width * 0.18f
        val dy = height * 0.16f
        path.reset()
        if (pointingUp) {
            path.moveTo(cx - dx, cy + dy)
            path.lineTo(cx, cy - dy)
            path.lineTo(cx + dx, cy + dy)
        } else {
            path.moveTo(cx - dx, cy - dy)
            path.lineTo(cx, cy + dy)
            path.lineTo(cx + dx, cy - dy)
        }
        canvas.drawPath(path, paint)
    }
}
