package io.github.franc417.mesh.stream

import org.jcodec.codecs.h264.H264Utils
import org.jcodec.codecs.h264.io.model.VUIParameters
import java.nio.ByteBuffer

/**
 * Rewrites the H.264 sequence parameter set (SPS) so the phone's decoder shows each frame as soon as it is decoded.
 *
 * Without "bitstream restrictions" in the SPS a decoder has to assume the stream may reorder frames and keep up to
 * a whole level's worth of frames buffered (many at 720p) before showing anything. That is delay you feel as lag even
 * when no frame is ever dropped. The stream from Sunshine never reorders, so we say so: one reference frame, zero
 * reordered frames, at most one frame buffered. This is the same rewrite Moonlight for Android applies on every
 * device from Android 8 on (see MediaCodecDecoderRenderer.submitDecodeUnit), with one difference: reordering is always
 * set to zero, which is right for a stream without B-frames and keeps the rewritten header valid.
 *
 * If anything about the SPS cannot be parsed, the original bytes are returned unchanged: a stream with some extra
 * delay is better than no stream.
 */
object H264SpsPatcher {

    /**
     * [nal] holds one SPS NAL unit in Annex B form (a 3- or 4-byte start code, the NAL header byte, then the payload),
     * of which the first [length] bytes are valid.
     */
    fun patch(nal: ByteArray, length: Int): ByteArray = tryPatch(nal, length).getOrElse { nal.copyOf(length) }

    /** Like [patch], but says whether it worked: a failure carries the reason, so it can be reported. */
    fun tryPatch(nal: ByteArray, length: Int): Result<ByteArray> = runCatching {
        val original = nal.copyOf(length)
        val startSeqLen = if (original[2] == 0x01.toByte()) 3 else 4
        val buf = ByteBuffer.wrap(original)
        buf.position(startSeqLen + 1) // skip the start code and the NAL header byte
        val sps = H264Utils.readSPS(buf) // handles emulation-prevention bytes

        sps.numRefFrames = 1
        if (sps.vuiParams == null) sps.vuiParams = VUIParameters()
        if (sps.vuiParams.bitstreamRestriction == null) {
            val r = VUIParameters.BitstreamRestriction()
            r.motionVectorsOverPicBoundariesFlag = true
            r.maxBytesPerPicDenom = 2
            r.maxBitsPerMbDenom = 1
            r.log2MaxMvLengthHorizontal = 16
            r.log2MaxMvLengthVertical = 16
            r.numReorderFrames = 0
            sps.vuiParams.bitstreamRestriction = r
        }
        // Sunshine never sends B-frames, so nothing is ever shown out of decode order. Setting this always (Moonlight
        // only sets it when it adds the restrictions) also keeps the header valid: reorder frames may not exceed
        // the buffering we declare next.
        sps.vuiParams.bitstreamRestriction.numReorderFrames = 0
        // Some decoders reject maxDecFrameBuffering < numRefFrames, so tie them together.
        sps.vuiParams.bitstreamRestriction.maxDecFrameBuffering = sps.numRefFrames

        val escaped = H264Utils.writeSPS(sps, length)
        val out = ByteArray(startSeqLen + 1 + escaped.limit())
        System.arraycopy(original, 0, out, 0, startSeqLen + 1)
        escaped.get(out, startSeqLen + 1, escaped.limit())
        out
    }
}
