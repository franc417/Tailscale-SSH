package io.github.franc417.mesh

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/** A computer this phone is paired with. */
data class Computer(
    val id: String,
    val name: String,
    val host: String,
    val port: Int,
    /** SHA-256 of the agent's certificate (hex): the only certificate this phone will accept for it. */
    val fingerprint: String,
    val tokenHex: String,
)

/**
 * Saved computers, in the app's private storage. Backups are switched off in the manifest so the
 * pairing tokens never leave the phone. (Wrapping the tokens with an Android Keystore key is a planned hardening step.)
 */
class ComputerStore(context: Context) {
    private val prefs = context.getSharedPreferences("mesh_computers", Context.MODE_PRIVATE)

    fun all(): List<Computer> {
        val raw = prefs.getString(KEY, null) ?: return emptyList()
        return try {
            val array = JSONArray(raw)
            (0 until array.length()).map { i ->
                val o = array.getJSONObject(i)
                Computer(o.getString("id"), o.getString("name"), o.getString("host"), o.getInt("port"), o.getString("fingerprint"), o.getString("token"))
            }
        } catch (e: org.json.JSONException) {
            emptyList()
        }
    }

    fun find(id: String?): Computer? = all().firstOrNull { it.id == id }

    fun add(computer: Computer) = save(all() + computer)

    fun remove(id: String) = save(all().filter { it.id != id })

    private fun save(list: List<Computer>) {
        val array = JSONArray()
        for (c in list) {
            array.put(JSONObject().put("id", c.id).put("name", c.name).put("host", c.host).put("port", c.port)
                .put("fingerprint", c.fingerprint).put("token", c.tokenHex))
        }
        prefs.edit().putString(KEY, array.toString()).apply()
    }

    private companion object {
        const val KEY = "computers"
    }
}
