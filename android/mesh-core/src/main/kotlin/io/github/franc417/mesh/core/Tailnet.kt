package io.github.franc417.mesh.core

import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress

/** What counts as a Tailscale address: 100.64.0.0/10 (IPv4) and fd7a:115c:a1e0::/48 (IPv6). */
object Tailnet {
    fun isTailnetAddress(address: InetAddress): Boolean {
        val b = address.address
        return when (address) {
            is Inet4Address -> (b[0].toInt() and 0xFF) == 100 && (b[1].toInt() and 0xC0) == 0x40
            is Inet6Address -> b.size == 16 && (b[0].toInt() and 0xFF) == 0xFD && (b[1].toInt() and 0xFF) == 0x7A &&
                (b[2].toInt() and 0xFF) == 0x11 && (b[3].toInt() and 0xFF) == 0x5C && (b[4].toInt() and 0xFF) == 0xA1 && (b[5].toInt() and 0xFF) == 0xE0
            else -> false
        }
    }

    /** True when [host] (an address or a MagicDNS name) resolves to a Tailscale address. Blocks on name lookup: not for the main thread. */
    fun isTailnetHost(host: String): Boolean =
        try {
            InetAddress.getAllByName(host).any { isTailnetAddress(it) }
        } catch (e: java.io.IOException) {
            false
        }

    /** The hosts that are on the Tailscale network, in the order given. */
    fun onlyTailnet(hosts: List<String>): List<String> = hosts.filter { isTailnetHost(it) }
}
