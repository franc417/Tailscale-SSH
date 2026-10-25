package io.github.franc417.mesh.core

import java.nio.ByteBuffer

/**
 * The Mesh input protocol (MIP v1), phone side. The host side is mesh_input.py; the
 * wire format is written down in docs/MESH-INPUT-PROTOCOL.md and both sides are tested
 * against the same byte vectors.
 *
 * Frame: u16 length (of type + payload, big-endian) | u8 type | payload.
 */
object Mip {
    const val VERSION = 1
    const val DEFAULT_PORT = 47600
    const val MAX_FRAME = 4096
    const val MAX_TEXT_BYTES = 1024
    const val TOKEN_BYTES = 32

    // phone -> host
    const val T_HELLO = 0x01
    const val T_MOVE = 0x02
    const val T_SCROLL = 0x03
    const val T_BUTTON = 0x04
    const val T_KEY = 0x05
    const val T_TEXT = 0x06
    const val T_PING = 0x07
    const val T_RELEASE_ALL = 0x08
    const val T_BYE = 0x09
    const val T_SUNSHINE_PAIR = 0x0A

    // host -> phone
    const val T_HELLO_OK = 0x81
    const val T_ERROR = 0x82
    const val T_PONG = 0x87
    const val T_SUNSHINE_RESULT = 0x8A

    const val E_BAD_TOKEN = 1
    const val E_BAD_VERSION = 2
    const val E_REPLACED = 3
    const val E_BAD_FRAME = 4
    const val E_NO_HELLO = 5

    const val FEATURE_TEXT = 0x01
    const val FEATURE_HIRES_SCROLL = 0x02
    const val FEATURE_SUNSHINE_PAIR = 0x04

    // T_SUNSHINE_RESULT status codes (the host agent's S_* values)
    const val SUNSHINE_OK = 0
    const val SUNSHINE_NOT_CONFIGURED = 1
    const val SUNSHINE_UNREACHABLE = 2
    const val SUNSHINE_LOGIN_REFUSED = 3
    const val SUNSHINE_FAILED = 4
    const val SUNSHINE_BAD_REQUEST = 5

    /** Not sent by the host: used when the answer never came because the connection to the computer ended. */
    const val SUNSHINE_CONNECTION_LOST = -1

    const val BUTTON_LEFT = 1
    const val BUTTON_MIDDLE = 2
    const val BUTTON_RIGHT = 3
    const val BUTTON_BACK = 4
    const val BUTTON_FORWARD = 5
}

/** A message from the host. */
sealed class HostMessage {
    data class HelloOk(val version: Int, val features: Int, val hostName: String) : HostMessage()
    data class Error(val code: Int, val text: String) : HostMessage()
    data class Pong(val id: Int) : HostMessage()

    /** The answer to a Sunshine pairing request: [status] is a Mip.SUNSHINE_* value, [text] says why in plain words. */
    data class SunshineResult(val status: Int, val text: String) : HostMessage() {
        val ok: Boolean get() = status == Mip.SUNSHINE_OK
    }
    data class Unknown(val type: Int) : HostMessage()
}

/** The host refused or the connection could not be set up. `code` is a Mip.E_* value, or 0 for a local failure. */
class MipException(val code: Int, message: String, cause: Throwable? = null) : Exception(message, cause)

object MipCodec {

    fun frame(type: Int, payload: ByteArray = ByteArray(0)): ByteArray {
        val size = 1 + payload.size
        require(size <= Mip.MAX_FRAME) { "frame too large ($size bytes)" }
        return ByteBuffer.allocate(2 + size).putShort(size.toShort()).put(type.toByte()).put(payload).array()
    }

    private fun short(data: ByteArray, what: String): ByteArray {
        require(data.size <= 255) { "$what too long" }
        return byteArrayOf(data.size.toByte()) + data
    }

    fun hello(token: ByteArray, deviceName: String, version: Int = Mip.VERSION): ByteArray =
        frame(Mip.T_HELLO, byteArrayOf(version.toByte()) + short(token, "token") + short(deviceName.toByteArray(Charsets.UTF_8).take(255).toByteArray(), "name"))

    fun move(dx: Float, dy: Float): ByteArray = frame(Mip.T_MOVE, ByteBuffer.allocate(8).putFloat(dx).putFloat(dy).array())

    fun scroll(vertical: Float, horizontal: Float = 0f): ByteArray =
        frame(Mip.T_SCROLL, ByteBuffer.allocate(8).putFloat(vertical).putFloat(horizontal).array())

    fun button(button: Int, down: Boolean): ByteArray = frame(Mip.T_BUTTON, byteArrayOf(button.toByte(), if (down) 1 else 0))

    fun key(code: String, down: Boolean): ByteArray =
        frame(Mip.T_KEY, byteArrayOf(if (down) 1 else 0) + short(code.toByteArray(Charsets.US_ASCII), "key code"))

    fun text(text: String): ByteArray {
        val data = text.toByteArray(Charsets.UTF_8)
        require(data.size <= Mip.MAX_TEXT_BYTES) { "text too long" }
        return frame(Mip.T_TEXT, data)
    }

    /** Splits long text into frames that fit, never cutting a character in half. */
    fun textChunks(text: String): List<String> {
        val chunks = mutableListOf<String>()
        val current = StringBuilder()
        var bytes = 0
        var i = 0
        while (i < text.length) {
            val cp = text.codePointAt(i)
            val piece = String(Character.toChars(cp))
            val n = piece.toByteArray(Charsets.UTF_8).size
            if (bytes + n > Mip.MAX_TEXT_BYTES) {
                chunks.add(current.toString())
                current.setLength(0)
                bytes = 0
            }
            current.append(piece)
            bytes += n
            i += Character.charCount(cp)
        }
        if (current.isNotEmpty()) chunks.add(current.toString())
        return chunks
    }

    fun ping(id: Int): ByteArray = frame(Mip.T_PING, ByteBuffer.allocate(4).putInt(id).array())

    fun releaseAll(): ByteArray = frame(Mip.T_RELEASE_ALL)

    fun bye(): ByteArray = frame(Mip.T_BYE)

    /** "Pair my desktop view with this PIN": [pin] is 4 digits, [name] what the computer should call this phone. */
    fun sunshinePair(pin: String, name: String): ByteArray {
        require(pin.length == 4 && pin.all { it in '0'..'9' }) { "the PIN must be 4 digits" }
        return frame(Mip.T_SUNSHINE_PAIR, short(pin.toByteArray(Charsets.US_ASCII), "pin") + short(name.take(60).toByteArray(Charsets.UTF_8), "name"))
    }

    fun decodeHost(type: Int, payload: ByteArray): HostMessage = when (type) {
        Mip.T_HELLO_OK -> {
            require(payload.size >= 3) { "HELLO_OK truncated" }
            val n = payload[2].toInt() and 0xFF
            require(payload.size >= 3 + n) { "HELLO_OK truncated" }
            HostMessage.HelloOk(payload[0].toInt() and 0xFF, payload[1].toInt() and 0xFF, String(payload, 3, n, Charsets.UTF_8))
        }
        Mip.T_ERROR -> {
            require(payload.size >= 2) { "ERROR truncated" }
            val n = payload[1].toInt() and 0xFF
            require(payload.size >= 2 + n) { "ERROR truncated" }
            HostMessage.Error(payload[0].toInt() and 0xFF, String(payload, 2, n, Charsets.UTF_8))
        }
        Mip.T_PONG -> {
            require(payload.size == 4) { "PONG: bad payload" }
            HostMessage.Pong(ByteBuffer.wrap(payload).int)
        }
        Mip.T_SUNSHINE_RESULT -> {
            require(payload.size >= 2) { "SUNSHINE_RESULT truncated" }
            val n = payload[1].toInt() and 0xFF
            require(payload.size >= 2 + n) { "SUNSHINE_RESULT truncated" }
            HostMessage.SunshineResult(payload[0].toInt() and 0xFF, String(payload, 2, n, Charsets.UTF_8))
        }
        else -> HostMessage.Unknown(type)
    }
}

fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

fun String.hexToBytes(): ByteArray {
    require(length % 2 == 0) { "odd length hex" }
    return ByteArray(length / 2) { ((Character.digit(this[it * 2], 16) shl 4) + Character.digit(this[it * 2 + 1], 16)).toByte() }
}
