package io.github.franc417.mesh

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView

/** The few colours and helpers the screens share: black and white, hairline borders, rounded corners. */
object Ui {
    val INK = Color.parseColor("#0B0B0C")
    val SURFACE = Color.parseColor("#141416")
    val BORDER = Color.parseColor("#2B2B30")
    val TEXT = Color.parseColor("#EDEDED")
    val MUTED = Color.parseColor("#8A8A92")
    val OK = Color.parseColor("#7BE0A1")
    val ERROR = Color.parseColor("#FF7B7B")

    fun dp(context: Context, value: Int): Int = (value * context.resources.displayMetrics.density).toInt()

    fun box(context: Context, fill: Int = SURFACE, stroke: Int = BORDER, radiusDp: Int = 14) = GradientDrawable().apply {
        setColor(fill)
        cornerRadius = dp(context, radiusDp).toFloat()
        setStroke(maxOf(1, dp(context, 1)), stroke)
    }

    fun text(context: Context, value: String, sizeSp: Float = 15f, color: Int = TEXT, bold: Boolean = false) = TextView(context).apply {
        text = value
        textSize = sizeSp
        setTextColor(color)
        if (bold) typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
    }

    fun button(context: Context, label: String, onClick: () -> Unit) = TextView(context).apply {
        text = label
        textSize = 14f
        setTextColor(TEXT)
        typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        gravity = Gravity.CENTER
        background = box(context)
        val h = dp(context, 16)
        val v = dp(context, 12)
        setPadding(h, v, h, v)
        setOnClickListener { onClick() }
    }

    fun gap(context: Context, heightDp: Int) = android.view.View(context).apply {
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(context, heightDp))
    }
}
