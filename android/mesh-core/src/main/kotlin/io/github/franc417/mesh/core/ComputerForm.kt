package io.github.franc417.mesh.core

/** What the add/edit screen accepts, and what it says when something cannot work. No Android API, so it is unit tested. */
object ComputerForm {

    class Invalid(message: String) : Exception(message)

    /**
     * Reads a typed list of addresses, separated by new lines, commas, semicolons or spaces, and checks each one with
     * [HostAddress.parse]. Duplicates are dropped, the order is kept. At least one address is required.
     */
    fun parseAddresses(text: String): List<String> {
        val parts = text.split(Regex("[\\s,;]+")).filter { it.isNotBlank() }
        if (parts.isEmpty()) throw Invalid("Enter at least one address for the computer")
        return parts.map { part ->
            try {
                HostAddress.parse(part)
            } catch (e: HostAddress.Invalid) {
                throw Invalid("$part: ${e.message}")
            }
        }.distinct()
    }

    /** The port typed by a person; an empty box keeps [current]. */
    fun parsePort(text: String, current: Int): Int {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return current
        val port = trimmed.toIntOrNull() ?: throw Invalid("The port must be a number")
        if (port !in 1..65535) throw Invalid("The port must be between 1 and 65535")
        return port
    }

    /** The name typed, or [fallback] when the box is empty. */
    fun parseName(text: String, fallback: String): String = text.trim().ifEmpty { fallback }

    /** An existing address list as it appears in the box: one address per line. */
    fun formatAddresses(hosts: List<String>): String = hosts.joinToString("\n")
}
