package io.github.franc417.mesh.core

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
        /**
         * Reads the agent's JSON. Tolerant on purpose, because people paste it from a terminal: extra text
         * around it, a prompt line, whitespace or colon-separated fingerprints are all fine. Strict about
         * the values: a wrong-length token or fingerprint is refused with a plain message.
         */
        fun parse(text: String): PairInfo {
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
