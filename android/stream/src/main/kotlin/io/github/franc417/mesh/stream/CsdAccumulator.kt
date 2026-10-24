package io.github.franc417.mesh.stream

/**
 * Collects the parameter sets (VPS, SPS, PPS) that arrive as separate decode units in front of an IDR frame.
 *
 * moonlight-common-c hands the decoder one unit per NAL group: for an IDR frame that is VPS (HEVC only), SPS, PPS and
 * then the picture data. MediaCodec wants the parameter sets together, in that order, as one codec-config buffer
 * queued before the first picture of the IDR. This class does only that bookkeeping and touches no Android API, so
 * it is unit tested on a plain JVM.
 */
class CsdAccumulator {
    private val vps = ArrayList<ByteArray>()
    private val sps = ArrayList<ByteArray>()
    private val pps = ArrayList<ByteArray>()
    private var currentFrame = Int.MIN_VALUE

    /** Call for every decode unit of an IDR frame. A new frame number starts a new set, so stale sets never leak. */
    fun beginFrame(frameNumber: Int) {
        if (frameNumber != currentFrame) {
            vps.clear()
            sps.clear()
            pps.clear()
            currentFrame = frameNumber
        }
    }

    /** Stores a parameter set. Returns false when [bufferType] is not a parameter set (i.e. picture data). */
    fun add(bufferType: Int, data: ByteArray, length: Int): Boolean {
        val target = when (bufferType) {
            BUFFER_TYPE_VPS -> vps
            BUFFER_TYPE_SPS -> sps
            BUFFER_TYPE_PPS -> pps
            else -> return false
        }
        target.add(data.copyOf(length))
        return true
    }

    val isEmpty: Boolean get() = vps.isEmpty() && sps.isEmpty() && pps.isEmpty()

    /** VPS, then SPS, then PPS, each with its own start code as received. */
    fun toByteArray(): ByteArray {
        var size = 0
        for (list in arrayOf(vps, sps, pps)) for (b in list) size += b.size
        val out = ByteArray(size)
        var pos = 0
        for (list in arrayOf(vps, sps, pps)) {
            for (b in list) {
                System.arraycopy(b, 0, out, pos, b.size)
                pos += b.size
            }
        }
        return out
    }

    fun clear() {
        vps.clear()
        sps.clear()
        pps.clear()
        currentFrame = Int.MIN_VALUE
    }

    companion object {
        // Same values as MoonBridge.BUFFER_TYPE_* (checked by a test), repeated here so this class needs no native library.
        const val BUFFER_TYPE_PICDATA = 0
        const val BUFFER_TYPE_SPS = 1
        const val BUFFER_TYPE_PPS = 2
        const val BUFFER_TYPE_VPS = 3
    }
}
