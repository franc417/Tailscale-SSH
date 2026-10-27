package io.github.franc417.mesh.stream

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class StreamReportTest {
    private fun sample(fps: Int, decodeMs: Float, hostMs: Float?, dropped: Int, rtt: Int?) =
        StreamReport.Sample(StreamStats.Snapshot(fps, decodeMs, hostMs, dropped), rtt)

    private fun input(
        samples: List<StreamReport.Sample> = emptyList(),
        patched: Int = 1,
        failed: Int = 0,
        error: String? = null,
        firstPictureMs: Long? = 1800,
        host: String = "192.168.100.4",
    ) = StreamReport.Input(
        phone = "Xiaomi Redmi A5", android = "15 (API 35)", decoder = "c2.unisoc.avc.decoder",
        spsPatched = patched, spsPatchFailed = failed, spsPatchError = error,
        settings = StreamSettings(), view = "Fill", pointer = "Auto", host = host, firstPictureMs = firstPictureMs,
        samples = samples, totalShown = 7306, totalDropped = 1,
    )

    @Test
    fun `host kinds`() {
        assertEquals("home network (LAN)", HostKind.of("192.168.100.4"))
        assertEquals("home network (LAN)", HostKind.of("10.0.0.7"))
        assertEquals("home network (LAN)", HostKind.of("172.16.5.5"))
        assertEquals("home network (LAN)", HostKind.of("172.31.255.1"))
        assertEquals("other", HostKind.of("172.32.0.1"))
        assertEquals("Tailscale (tunnel)", HostKind.of("100.67.163.21"))
        assertEquals("Tailscale (tunnel)", HostKind.of("100.64.0.1"))
        assertEquals("Tailscale (tunnel)", HostKind.of("100.127.255.254"))
        assertEquals("other", HostKind.of("100.128.0.1"))
        assertEquals("other", HostKind.of("8.8.8.8"))
        assertEquals("link-local (cable)", HostKind.of("169.254.10.2"))
        assertEquals("this phone itself", HostKind.of("127.0.0.1"))
        assertEquals("a name", HostKind.of("mkz.tailnet.ts.net"))
        assertEquals("IPv6 or a name", HostKind.of("fe80::1"))
        assertEquals("a name", HostKind.of("300.1.1.1"))
    }

    @Test
    fun `the averages and worst cases come from the one second samples`() {
        val text = StreamReport.build(input(listOf(
            sample(60, 8f, 4.0f, 0, 10),
            sample(50, 12f, 6.0f, 2, 30),
            sample(40, 16f, null, 1, null),
        )))
        assertTrue(text, text.contains("last 3 s:"))
        assertTrue(text, text.contains("frames per second: average 50, lowest 40"))
        assertTrue(text, text.contains("phone decode time: average 12 ms, worst second 16 ms"))
        assertTrue(text, text.contains("computer encode time: average 5.0 ms, worst second 6.0 ms"))   // the null second is left out
        assertTrue(text, text.contains("network round trip: average 20 ms, worst 30 ms"))
        assertTrue(text, text.contains("frames dropped: 3"))
        assertTrue(text, text.contains("7306 frames shown, 1 dropped"))
    }

    @Test
    fun `an unreported encode time and unknown round trip are said so`() {
        val text = StreamReport.build(input(listOf(sample(60, 5f, null, 0, null), sample(60, 5f, null, 0, 0))))
        assertTrue(text, text.contains("computer encode time: not reported"))
        assertTrue(text, text.contains("network round trip: unknown"))
    }

    @Test
    fun `no samples yet says so instead of printing zeros`() {
        val text = StreamReport.build(input())
        assertTrue(text, text.contains("no statistics yet"))
        assertFalse(text, text.contains("frames per second"))
    }

    @Test
    fun `the header rewrite status is explicit in all four cases`() {
        assertTrue(StreamReport.build(input(patched = 3)).contains("applied (3x)"))
        assertTrue(StreamReport.build(input(patched = 2, failed = 1, error = "boom")).contains("applied 2x, failed 1x (boom)"))
        val failedOnly = StreamReport.build(input(patched = 0, failed = 4, error = "IndexOutOfBounds"))
        assertTrue(failedOnly, failedOnly.contains("FAILED 4x (IndexOutOfBounds)") && failedOnly.contains("default delay"))
        assertTrue(StreamReport.build(input(patched = 0)).contains("not seen"))
    }

    @Test
    fun `the setup lines name the phone the stream the address and the first picture`() {
        val text = StreamReport.build(input())
        assertTrue(text, text.contains("phone: Xiaomi Redmi A5, Android 15 (API 35)"))
        assertTrue(text, text.contains("decoder: c2.unisoc.avc.decoder"))
        assertTrue(text, text.contains("stream: 1280x720 at 60 fps, 10000 kbps, H.264; view Fill, pointer Auto"))
        assertTrue(text, text.contains("computer: 192.168.100.4 = home network (LAN)"))
        assertTrue(text, text.contains("first picture after: 1.8 s"))
        assertTrue(StreamReport.build(input(firstPictureMs = null)).contains("first picture after: never"))
        assertTrue(StreamReport.build(input(host = "100.67.163.21")).contains("= Tailscale (tunnel)"))
    }

    @Test
    fun `no decoder yet is not printed as an empty name`() {
        val text = StreamReport.build(input().copy(decoder = ""))
        assertTrue(text, text.contains("decoder: none yet"))
    }
}
