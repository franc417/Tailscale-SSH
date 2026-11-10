package io.github.franc417.mesh.core

import java.net.URLDecoder
import java.util.Base64

/** Everything the phone needs to talk to one computer's agent, as printed by `mesh_input.py pair-info`. */
data class PairInfo(
    val version: Int,
    val port: Int,
    /** SHA-256 of the agent's certificate, 64 lowercase hex characters. */
    val fingerprint: String,
    /** The pairing token, 64 hex characters (32 bytes). */
    val tokenHex: String,
    /** The computer's own name, if it reported one. */
    val hostName: String,
    /** Addresses the computer says it can be reached at, best first (Tailscale, then LAN). May be empty. */
    val addresses: List<String> = emptyList(),
) {
    val token: ByteArray get() = tokenHex.hexToBytes()

    class ParseException(message: String) : Exception(message)

    companion object {
        /** The short form `meshd pair` puts in its QR code: mesh://pair?v=1&p=PORT&f=FINGERPRINT&t=TOKEN&h=NAME&a=ADDRESS,ADDRESS */
        private const val LINK = "mesh://pair?"

        /** True for text that is, or contains, either form of pairing info: what the scanner accepts and anything else it ignores. */
        fun looksLikePairing(text: String): Boolean =
            text.contains(LINK) || (text.contains("\"fingerprint\"") && text.contains("\"token\""))

        /**
         * Reads the short link from the QR code: the fingerprint and the token are base64url (32 bytes each), the name is URL-escaped,
         * the addresses are comma separated. Anything unusable is refused with a plain message.
         */
        private fun parseLink(text: String): PairInfo {
            val query = text.substring(text.indexOf(LINK) + LINK.length).takeWhile { !it.isWhitespace() && it != '"' }
            val params = HashMap<String, String>()
            for (part in query.split('&')) {
                val eq = part.indexOf('=')
                if (eq > 0) {
                    val raw = part.substring(eq + 1)
                    params[part.substring(0, eq)] = try { URLDecoder.decode(raw, "UTF-8") } catch (e: IllegalArgumentException) { raw }
                }
            }
            fun hexOf(name: String, what: String): String {
                val raw = params[name] ?: throw ParseException("the pairing link has no $what")
                val bytes = try {
                    Base64.getUrlDecoder().decode(raw)
                } catch (e: IllegalArgumentException) {
                    throw ParseException("the $what in the pairing link is damaged: scan the code again")
                }
                if (bytes.size != 32) throw ParseException("the $what in the pairing link should be 32 bytes")
                return bytes.joinToString("") { "%02x".format(it) }
            }

            val version = params["v"]?.toIntOrNull() ?: throw ParseException("the pairing link has no version")
            if (version != Mip.VERSION) throw ParseException("this pairing info is for protocol version $version; this app speaks ${Mip.VERSION} (update the app or the agent)")
            val port = params["p"]?.toIntOrNull()?.takeIf { it in 1..65535 } ?: throw ParseException("the pairing link has no valid port")
            val addresses = params["a"].orEmpty().split(',').map { it.trim() }.filter { it.isNotEmpty() && HostAddress.isUsable(it) }.distinct()
            return PairInfo(version, port, hexOf("f", "certificate fingerprint"), hexOf("t", "pairing token"), params["h"].orEmpty(), addresses)
        }

        /**
         * Reads the agent's JSON. Tolerant on purpose, because people paste it from a terminal: extra text
         * around it, a prompt line, whitespace or colon-separated fingerprints are all fine. Strict about
         * the values: a wrong-length token or fingerprint is refused with a plain message.
         */
        fun parse(text: String): PairInfo {
            if (text.contains(LINK)) return parseLink(text)
            fun field(name: String): String? =
                Regex("\"$name\"\\s*:\\s*(\"([^\"]*)\"|(-?[0-9]+))").find(text)?.let { it.groupValues[2].ifEmpty { it.groupValues[3] } }

            if (!text.contains("\"fingerprint\"") && !text.contains("\"token\"")) {
                throw ParseException("that is not pairing info: run  python3 mesh_input.py pair-info  on the computer and paste what it prints")
            }
            val version = field("version")?.toIntOrNull() ?: throw ParseException("the pairing info has no version")
            if (version != Mip.VERSION) throw ParseException("this pairing info is for protocol version $version; this app speaks ${Mip.VERSION} (update the app or the agent)")
            val port = field("port")?.toIntOrNull()?.takeIf { it in 1..65535 } ?: throw ParseException("the pairing info has no valid port")
            val fingerprint = field("fingerprint")?.let { PinnedTls.normalise(it) }
                ?.takeIf { it.matches(Regex("[0-9a-f]{64}")) } ?: throw ParseException("the certificate fingerprint should be 64 hex characters")
            val token = field("token")?.trim()?.lowercase()
                ?.takeIf { it.matches(Regex("[0-9a-f]{64}")) } ?: throw ParseException("the pairing token should be 64 hex characters")
            val addresses = Regex("\"addresses\"\\s*:\\s*\\[(.*?)\\]", RegexOption.DOT_MATCHES_ALL).find(text)?.groupValues?.get(1)
                ?.let { list -> Regex("\"([^\"]+)\"").findAll(list).map { it.groupValues[1] }.toList() }.orEmpty()
                .filter { HostAddress.isUsable(it) }.distinct()
            return PairInfo(version, port, fingerprint, token, field("host").orEmpty(), addresses)
        }
    }
}
