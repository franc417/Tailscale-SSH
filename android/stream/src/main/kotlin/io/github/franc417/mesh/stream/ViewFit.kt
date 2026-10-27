package io.github.franc417.mesh.stream

/** How the streamed picture is placed on the phone's screen. */
enum class ViewMode {
    /** The whole picture, undistorted; black bars where the shapes differ. */
    FIT,

    /** The whole picture over the whole screen: nothing cropped, no bars, but stretched a little where the shapes differ. */
    FILL,

    /** Zoomed until it covers the whole screen, undistorted; whatever hangs over the edges is cropped. */
    ZOOM;

    companion object {
        /** Reads a saved name. "STRETCH" is what an earlier version called what is now FILL; anything unknown is FIT. */
        fun parse(name: String?): ViewMode = when (name) {
            "FILL", "STRETCH" -> FILL
            "ZOOM" -> ZOOM
            else -> FIT
        }
    }
}

/** The sizing rules for [ViewMode]. No Android API, so they are unit tested. */
object ViewFit {
    data class Size(val width: Int, val height: Int)

    /**
     * The size the picture should be drawn at on a stage of [stageW] x [stageH] pixels, for a picture whose shape is
     * [aspect] (width divided by height). It is centred by the caller; with [ViewMode.ZOOM] it is larger than the stage.
     * A stage or aspect that makes no sense gives the stage size back, so a layout pass before measuring is harmless.
     */
    fun size(mode: ViewMode, stageW: Int, stageH: Int, aspect: Float): Size {
        if (stageW <= 0 || stageH <= 0 || aspect <= 0f || aspect.isNaN() || aspect.isInfinite()) return Size(maxOf(stageW, 0), maxOf(stageH, 0))
        return when (mode) {
            ViewMode.FILL -> Size(stageW, stageH)
            ViewMode.FIT -> {
                val byWidth = Size(stageW, Math.round(stageW / aspect))
                if (byWidth.height <= stageH) byWidth else Size(Math.round(stageH * aspect), stageH)
            }
            ViewMode.ZOOM -> {
                val byWidth = Size(stageW, Math.round(stageW / aspect))
                if (byWidth.height >= stageH) byWidth else Size(Math.round(stageH * aspect), stageH)
            }
        }
    }
}
