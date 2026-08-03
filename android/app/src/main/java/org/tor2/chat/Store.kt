package org.tor2.chat

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Saved servers, contacts and settings.
 *
 * Everything lives in the app's private directory, which Android keeps out of
 * reach of other apps.
 */
class Store(context: Context) {
    private val dir = File(context.filesDir, "tor2").apply { mkdirs() }
    private val serversFile = File(dir, "servers.json")
    private val contactsFile = File(dir, "contacts.json")
    private val settingsFile = File(dir, "settings.json")
    private val onionKeyFile = File(dir, "onion.key")
    val mediaDir: File = File(context.filesDir, "media").apply { mkdirs() }

    var nick: String
        get() = settings().optString("nick", "").ifBlank { "phone" }
        set(value) = saveSetting("nick", value)

    fun servers(): List<SavedServer> = read(serversFile).let { json ->
        json.keys().asSequence().map { key ->
            val o = json.getJSONObject(key)
            SavedServer(key, o.optString("onion"), o.optString("token"),
                        o.optString("name", key))
        }.toList()
    }

    fun saveServer(s: SavedServer) {
        val json = read(serversFile)
        json.put(s.key, JSONObject().apply {
            put("onion", s.onion); put("token", s.token); put("name", s.displayName)
        })
        serversFile.writeText(json.toString())
    }

    fun removeServer(key: String) {
        val json = read(serversFile)
        json.remove(key)
        serversFile.writeText(json.toString())
    }

    fun contacts(): List<Contact> = read(contactsFile).let { json ->
        json.keys().asSequence().map { Contact(it, json.getString(it)) }.toList()
    }

    fun saveContact(c: Contact) {
        val json = read(contactsFile)
        json.put(c.name, c.onion)
        contactsFile.writeText(json.toString())
    }

    fun removeContact(name: String) {
        val json = read(contactsFile)
        json.remove(name)
        contactsFile.writeText(json.toString())
    }

    fun settings(): JSONObject = read(settingsFile)

    fun saveSetting(key: String, value: Any) {
        val json = read(settingsFile)
        json.put(key, value)
        settingsFile.writeText(json.toString())
    }

    fun bool(key: String, fallback: Boolean): Boolean =
        settings().optBoolean(key, fallback)

    var onionKey: String?
        get() = onionKeyFile.takeIf { it.isFile }?.readText()?.ifBlank { null }
        set(value) { if (value != null) onionKeyFile.writeText(value) }

    private fun read(file: File): JSONObject = runCatching {
        if (file.isFile) JSONObject(file.readText()) else JSONObject()
    }.getOrDefault(JSONObject())
}
