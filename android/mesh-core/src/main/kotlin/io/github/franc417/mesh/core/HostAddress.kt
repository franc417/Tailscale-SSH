package io.github.franc417.mesh.core

import java.util.concurrent.Callable
import java.util.concurrent.ExecutorCompletionService
import java.util.concurrent.Executors
import java.net.InetSocketAddress
import java.net.Socket

/** Checks an address typed by a person, and explains in plain words when it cannot work. */
object HostAddress {

    class Invalid(message: String) : Exception(message)

    private val HOSTNAME = Regex("^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$")
    private val DOTTED = Regex("^[0-9]+(\\.[0-9]+){3}$")

    /**
     * Returns the host to use (a name or IPv4 address), or throws Invalid with a message you can show.
     * A scheme, a trailing slash and a port are tolerated and removed (the port comes from the pairing info).
     */
    fun parse(input: String): String {
        var text = input.trim()
        for (scheme in listOf("ws://", "wss://", "tcp://", "http://", "https://")) {
            if (text.startsWith(scheme, ignoreCase = true)) text = text.substring(scheme.length)
        }
        text = text.trimEnd('/')
        if (text.matches(Regex(".*:[0-9]{1,5}$")) && !text.contains("::")) text = text.substringBeforeLast(':')
        if (text.isEmpty()) throw Invalid("enter the computer's address")
        if (text.any { it.isWhitespace() }) throw Invalid("an address has no spaces in it")

        val lower = text.lowercase()
        if (text == "0.0.0.0") {
            throw Invalid("0.0.0.0 means \"every address\" and is only what the agent prints while it listens. Use the computer's real address, such as the one pair-info lists.")
        }
        if (lower == "localhost" || text.startsWith("127.") || text == "::1" || text == "::") {
            throw Invalid("$text is this phone itself, not the computer. Use the computer's address.")
        }
        if (text.startsWith("169.254.")) throw Invalid("$text is a link-local address and cannot be reached over the network.")
        if (text.contains(':')) throw Invalid("IPv6 addresses are not supported yet; use the computer's IPv4 address or its name.")
        if (DOTTED.matches(text)) {
            if (text.split('.').any { it.toInt() > 255 }) throw Invalid("$text is not a valid IP address")
            return text
        }
        if (!HOSTNAME.matches(text)) throw Invalid("\"$text\" is not a valid name or IP address")
        return text
    }

    /** True if [parse] accepts it; for filtering lists that came from the computer itself. */
    fun isUsable(candidate: String): Boolean = try {
        parse(candidate)
        true
    } catch (e: Invalid) {
        false
    }
}

object Reach {

    /**
     * The first host that accepts a connection on [port], trying them all at once so a computer that is
     * unreachable (say, its home LAN address while you are away) never delays the one that works.
     * Returns null if none answers within [timeoutMs].
     */
    fun firstReachable(hosts: List<String>, port: Int, timeoutMs: Int): String? {
        if (hosts.isEmpty()) return null
        val pool = Executors.newFixedThreadPool(hosts.size) { r -> Thread(r, "mesh-reach").apply { isDaemon = true } }
        try {
            val done = ExecutorCompletionService<String?>(pool)
            for (host in hosts) {
                done.submit(Callable {
                    try {
                        Socket().use { it.connect(InetSocketAddress(host, port), timeoutMs) }
                        host
                    } catch (e: Exception) {
                        null
                    }
                })
            }
            repeat(hosts.size) {
                done.take().get()?.let { return it }
            }
            return null
        } finally {
            pool.shutdownNow()
        }
    }
}
