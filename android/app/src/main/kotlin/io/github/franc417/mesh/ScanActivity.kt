package io.github.franc417.mesh

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.os.Bundle
import android.util.Size
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.FocusMeteringAction
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.ReaderException
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeReader
import io.github.franc417.mesh.core.PairInfo
import io.github.franc417.mesh.core.ScanGeometry
import java.util.concurrent.Executors

/**
 * Scans the QR code that `meshd pair` prints on the computer, and hands its text back to the form (RESULT_OK with EXTRA_TEXT).
 * It is the way in for a phone without Tailscale: one scan carries the computer's addresses, certificate fingerprint and token.
 *
 * What the person sees is a camera picture with a square in the middle; everything outside the square is dimmed. The decoder reads
 * ONLY inside that square (see ScanGeometry), so "keep the code in the square" is literally true, and the code fills the part the
 * camera focuses on. Tapping the picture focuses where you tap. Decoding is ZXing (no Google services needed).
 */
class ScanActivity : ComponentActivity() {

    private lateinit var previewView: PreviewView
    private lateinit var status: TextView
    private val analysisExecutor = Executors.newSingleThreadExecutor()
    private val reader = QRCodeReader()
    private val hints: Map<DecodeHintType, Any> = mapOf(DecodeHintType.TRY_HARDER to true, DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE))
    private var camera: Camera? = null
    @Volatile private var finished = false

    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startCamera() else giveUp("Mesh needs the camera to scan the code. You can paste the pairing info instead.")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = FrameLayout(this)
        previewView = PreviewView(this).apply {
            scaleType = PreviewView.ScaleType.FILL_CENTER
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
            setOnTouchListener { view, event ->
                if (event.action == MotionEvent.ACTION_UP) {
                    val point = meteringPointFactory.createPoint(event.x, event.y)
                    camera?.cameraControl?.startFocusAndMetering(FocusMeteringAction.Builder(point).build())
                    view.performClick()
                }
                true
            }
        }
        root.addView(previewView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        root.addView(ScanOverlay(this), FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val pad = Ui.dp(this@ScanActivity, 20)
            setPadding(pad, pad, pad, pad)
        }
        status = Ui.text(this, "Point the camera at the QR code and keep it inside the square.", 15f, Ui.TEXT, bold = true).apply { gravity = Gravity.CENTER }
        panel.addView(status, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        panel.addView(Ui.gap(this, 6))
        panel.addView(
            Ui.text(this, "Hold the phone 15 to 25 cm away and tap the picture to focus. No code yet? On the computer run:  meshd pair", 12f, Ui.MUTED).apply { gravity = Gravity.CENTER },
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT),
        )
        panel.addView(Ui.gap(this, 14))
        panel.addView(Ui.button(this, "Cancel") { setResult(Activity.RESULT_CANCELED); finish() })
        root.addView(panel, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))
        setContentView(root)

        if (checkSelfPermission(Manifest.permission.CAMERA) == android.content.pm.PackageManager.PERMISSION_GRANTED) startCamera() else cameraPermission.launch(Manifest.permission.CAMERA)
    }

    override fun onDestroy() {
        super.onDestroy()
        analysisExecutor.shutdown()
    }

    private fun giveUp(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
        setResult(Activity.RESULT_CANCELED)
        finish()
    }

    private fun startCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            try {
                val provider = future.get()
                val preview = Preview.Builder().build().also { it.setSurfaceProvider(previewView.surfaceProvider) }
                val selector = ResolutionSelector.Builder()
                    .setResolutionStrategy(ResolutionStrategy(Size(1280, 720), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
                    .build()
                val analysis = ImageAnalysis.Builder()
                    .setResolutionSelector(selector)
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                analysis.setAnalyzer(analysisExecutor) { image -> analyze(image) }
                provider.unbindAll()
                camera = provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
            } catch (e: Exception) {
                giveUp("Could not start the camera (${e.message}). You can paste the pairing info instead.")
            }
        }, ContextCompat.getMainExecutor(this))
    }

    /** Looks for a QR code inside the square of one camera picture. Runs on the analysis thread; always releases the picture. */
    private fun analyze(image: ImageProxy) {
        try {
            if (finished) return
            val plane = image.planes[0]
            val rowStride = plane.rowStride
            val buffer = plane.buffer
            val bytes = ByteArray(maxOf(buffer.remaining(), rowStride * image.height))
            buffer.get(bytes, 0, buffer.remaining())
            val side = ScanGeometry.squareSide(image.width, image.height, image.imageInfo.rotationDegrees, previewView.width, previewView.height)
            val left = ScanGeometry.origin(image.width, side)
            val top = ScanGeometry.origin(image.height, side)
            val source = PlanarYUVLuminanceSource(bytes, rowStride, image.height, left, top, side, side, false)
            val text = try {
                reader.decode(BinaryBitmap(HybridBinarizer(source)), hints).text
            } catch (e: ReaderException) {
                null      // no code in this picture: normal, the next one is tried
            } finally {
                reader.reset()
            }
            if (text != null) onText(text)
        } catch (e: Exception) {
            // a bad frame must never end the scan
        } finally {
            image.close()
        }
    }

    private fun onText(text: String) {
        if (!PairInfo.looksLikePairing(text)) {
            runOnUiThread { status.text = "That QR code is not from Mesh. Scan the one printed by  meshd pair." }
            return
        }
        try {
            PairInfo.parse(text)
        } catch (e: PairInfo.ParseException) {
            runOnUiThread { status.text = e.message ?: "That pairing code cannot be used." }
            return
        }
        if (finished) return
        finished = true
        runOnUiThread {
            setResult(Activity.RESULT_OK, Intent().putExtra(EXTRA_TEXT, text))
            finish()
        }
    }

    companion object {
        const val EXTRA_TEXT = "text"
    }
}

/** Dims everything but a centred square and draws its four corners, so the person knows where to hold the code. */
class ScanOverlay(context: Context) : View(context) {
    private val dim = Paint().apply { color = Color.argb(150, 0, 0, 0) }
    private val frame = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(90, 255, 255, 255)
        style = Paint.Style.STROKE
        strokeWidth = Ui.dp(context, 1).toFloat()
    }
    private val corner = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        style = Paint.Style.STROKE
        strokeWidth = Ui.dp(context, 4).toFloat()
        strokeCap = Paint.Cap.ROUND
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        val side = ScanGeometry.FRACTION * minOf(w, h)
        val left = (w - side) / 2
        val top = (h - side) / 2
        val right = left + side
        val bottom = top + side
        canvas.drawRect(0f, 0f, w, top, dim)
        canvas.drawRect(0f, bottom, w, h, dim)
        canvas.drawRect(0f, top, left, bottom, dim)
        canvas.drawRect(right, top, w, bottom, dim)
        canvas.drawRect(RectF(left, top, right, bottom), frame)
        val len = side * 0.14f
        for ((x, dx) in listOf(left to 1f, right to -1f)) {
            for ((y, dy) in listOf(top to 1f, bottom to -1f)) {
                canvas.drawLine(x, y, x + dx * len, y, corner)
                canvas.drawLine(x, y, x, y + dy * len, corner)
            }
        }
    }
}
