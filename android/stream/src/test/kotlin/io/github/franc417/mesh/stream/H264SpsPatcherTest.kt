package io.github.franc417.mesh.stream

import org.jcodec.codecs.h264.H264Utils
import org.jcodec.codecs.h264.io.model.SeqParameterSet
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer

class H264SpsPatcherTest {
    // An x264 SPS (checked bit by bit offline): High profile, level 3.1, 1280x720 (80 x 45 macroblocks), num_ref_frames 4,
    // VUI with timing info AND bitstream restrictions (num_reorder_frames 2, max_dec_frame_buffering 4).
    // Header byte 0x67 = NAL type 7 (SPS).
    private val spsPayload = intArrayOf(
        0x67, 0x64, 0x00, 0x1f, 0xac, 0xd9, 0x40, 0x50, 0x05, 0xbb, 0x01, 0x10, 0x00, 0x00, 0x03, 0x00,
        0x10, 0x00, 0x00, 0x03, 0x03, 0xc0, 0xf1, 0x83, 0x19, 0x60,
    )

    private fun annexB(startCode: Int): ByteArray {
        val prefix = if (startCode == 3) intArrayOf(0, 0, 1) else intArrayOf(0, 0, 0, 1)
        return (prefix + spsPayload).map { it.toByte() }.toByteArray()
    }

    private fun parse(annexB: ByteArray, startCodeLen: Int): SeqParameterSet {
        val buf = ByteBuffer.wrap(annexB)
        buf.position(startCodeLen + 1)
        return H264Utils.readSPS(buf)
    }

    @Test
    fun theTestVectorParsesAsExpected() {
        val sps = parse(annexB(4), 4)
        assertEquals(100, sps.profileIdc)
        assertEquals(31, sps.levelIdc)
        assertEquals(79, sps.picWidthInMbsMinus1)
        assertEquals(44, sps.picHeightInMapUnitsMinus1)
        assertEquals(4, sps.numRefFrames)
        assertNotNull("the vector has VUI", sps.vuiParams)
        assertNotNull(sps.vuiParams.bitstreamRestriction)
        assertEquals(2, sps.vuiParams.bitstreamRestriction.numReorderFrames)
        assertEquals(4, sps.vuiParams.bitstreamRestriction.maxDecFrameBuffering)
    }

    /** Builds an Annex B SPS from the vector after [edit] changed it, using jcodec's own writer. */
    private fun variant(edit: (SeqParameterSet) -> Unit): ByteArray {
        val sps = parse(annexB(4), 4)
        edit(sps)
        val escaped = H264Utils.writeSPS(sps, 64)
        val out = ByteArray(5 + escaped.limit())
        System.arraycopy(annexB(4), 0, out, 0, 5)
        escaped.get(out, 5, escaped.limit())
        return out
    }

    @Test
    fun anSpsWithVuiButNoRestrictionsGetsThemAdded() {
        val input = variant { it.vuiParams.bitstreamRestriction = null }
        assertEquals(null, parse(input, 4).vuiParams.bitstreamRestriction)
        val sps = parse(H264SpsPatcher.patch(input, input.size), 4)
        assertEquals(1, sps.numRefFrames)
        assertEquals(0, sps.vuiParams.bitstreamRestriction.numReorderFrames)
        assertEquals(1, sps.vuiParams.bitstreamRestriction.maxDecFrameBuffering)
    }

    @Test
    fun anSpsWithNoVuiAtAllGetsOneWithRestrictions() {
        val input = variant { it.vuiParams = null }
        assertEquals(null, parse(input, 4).vuiParams)
        val sps = parse(H264SpsPatcher.patch(input, input.size), 4)
        assertEquals(1, sps.numRefFrames)
        assertEquals(0, sps.vuiParams.bitstreamRestriction.numReorderFrames)
        assertEquals(1, sps.vuiParams.bitstreamRestriction.maxDecFrameBuffering)
        assertEquals(79, sps.picWidthInMbsMinus1)
    }

    @Test
    fun patchingAddsLowLatencyRestrictionsAndKeepsThePicture() {
        val input = annexB(4)
        val patched = H264SpsPatcher.patch(input, input.size)
        val sps = parse(patched, 4)

        assertEquals(1, sps.numRefFrames)
        assertNotNull(sps.vuiParams.bitstreamRestriction)
        assertEquals(0, sps.vuiParams.bitstreamRestriction.numReorderFrames)
        assertEquals(1, sps.vuiParams.bitstreamRestriction.maxDecFrameBuffering)
        // The picture itself is untouched.
        assertEquals(100, sps.profileIdc)
        assertEquals(31, sps.levelIdc)
        assertEquals(79, sps.picWidthInMbsMinus1)
        assertEquals(44, sps.picHeightInMapUnitsMinus1)
    }

    @Test
    fun startCodeAndNalHeaderAreKeptByteForByte() {
        val input = annexB(4)
        val patched = H264SpsPatcher.patch(input, input.size)
        assertArrayEquals(input.copyOf(5), patched.copyOf(5))
    }

    @Test
    fun aThreeByteStartCodeWorksToo() {
        val input = annexB(3)
        val patched = H264SpsPatcher.patch(input, input.size)
        assertArrayEquals(input.copyOf(4), patched.copyOf(4))
        val sps = parse(patched, 3)
        assertEquals(1, sps.numRefFrames)
        assertEquals(1, sps.vuiParams.bitstreamRestriction.maxDecFrameBuffering)
    }

    @Test
    fun patchingTwiceGivesTheSameMeaning() {
        val input = annexB(4)
        val once = H264SpsPatcher.patch(input, input.size)
        val twice = H264SpsPatcher.patch(once, once.size)
        val a = parse(once, 4)
        val b = parse(twice, 4)
        assertEquals(a.numRefFrames, b.numRefFrames)
        assertEquals(a.vuiParams.bitstreamRestriction.numReorderFrames, b.vuiParams.bitstreamRestriction.numReorderFrames)
        assertEquals(a.vuiParams.bitstreamRestriction.maxDecFrameBuffering, b.vuiParams.bitstreamRestriction.maxDecFrameBuffering)
    }

    @Test
    fun onlyTheFirstLengthBytesAreUsed() {
        val input = annexB(4)
        val padded = input + ByteArray(40) { 0x55 }
        val patched = H264SpsPatcher.patch(padded, input.size)
        assertEquals(1, parse(patched, 4).numRefFrames)
    }

    @Test
    fun unparsableInputIsReturnedUnchangedInsteadOfBreakingTheStream() {
        // Nothing after the start code: reading the SPS must fail, and the input must come back as it was.
        val garbage = byteArrayOf(0, 0, 0, 1)
        assertArrayEquals(garbage, H264SpsPatcher.patch(garbage, garbage.size))
        val tiny = byteArrayOf(1)
        assertArrayEquals(tiny, H264SpsPatcher.patch(tiny, tiny.size))
    }

    @Test
    fun patchedHeaderStaysAboutTheSameSize() {
        val input = annexB(4)
        val patched = H264SpsPatcher.patch(input, input.size)
        assertTrue("size went from ${input.size} to ${patched.size}", patched.size in input.size - 4..input.size + 16)
    }
}
