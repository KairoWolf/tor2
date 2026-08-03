package org.tor2.chat

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/** Everything the UI observes, and every action it can take. */
class AppViewModel(app: Application) : AndroidViewModel(app) {

    val store = Store(app)
    val tor = TorNet(app)

    val torReady = MutableStateFlow(false)
    val bootstrap = tor.bootstrapPercent
    val nick = MutableStateFlow(store.nick)
    val savedServers = MutableStateFlow(store.servers())
    val contacts = MutableStateFlow(store.contacts())
    val current = MutableStateFlow<ServerClient?>(null)
    val banner = MutableStateFlow<String?>(null)
    val myAddress = MutableStateFlow<String?>(null)

    init {
        viewModelScope.launch {
            runCatching { tor.awaitReady() }
                .onSuccess {
                    torReady.value = true
                    banner.value = null
                    savedServers.value.firstOrNull()?.let { open(it) }
                }
                .onFailure { banner.value = "Tor could not start: ${it.message}" }
        }
    }

    fun setNick(value: String) {
        val clean = value.trim().take(32).filter { it.isLetterOrDigit() || it in "-_ " }
        if (clean.isBlank()) return
        nick.value = clean
        store.nick = clean
    }

    /** Join a server for the first time, with an invite code. */
    fun joinServer(onion: String, invite: String, label: String) {
        val normalized = onion.trim().lowercase()
            .removePrefix("http://").removePrefix("https://").trimEnd('/')
            .removeSuffix(".onion") + ".onion"
        if (!Regex("^[a-z2-7]{56}\\.onion$").matches(normalized)) {
            banner.value = "that does not look like a v3 onion address"
            return
        }
        val key = label.ifBlank { normalized.take(12) }
        val saved = SavedServer(key, normalized, "", label.ifBlank { key })
        store.saveServer(saved)
        savedServers.value = store.servers()
        connect(saved, invite)
    }

    fun open(saved: SavedServer) = connect(saved, "")

    private fun connect(saved: SavedServer, invite: String) {
        current.value?.disconnect()
        val client = ServerClient(tor, viewModelScope, store, nick.value, saved)
        current.value = client
        viewModelScope.launch { client.connect(invite) }
    }

    fun leave(saved: SavedServer) {
        current.value?.disconnect()
        current.value = null
        store.removeServer(saved.key)
        savedServers.value = store.servers()
    }

    fun send(text: String) {
        val client = current.value ?: return
        viewModelScope.launch { client.post(text) }
    }

    fun switchChannel(name: String) {
        viewModelScope.launch { current.value?.switchChannel(name) }
    }

    fun loadOlder() {
        viewModelScope.launch { current.value?.loadOlder() }
    }

    fun preview(id: Int) {
        viewModelScope.launch { current.value?.preview(id) }
    }

    fun download(id: Int) {
        viewModelScope.launch { current.value?.fetch(id) }
    }

    fun deleteMessage(id: Int) {
        viewModelScope.launch { current.value?.deleteMessage(id) }
    }

    fun admin(type: String, arg: String = "", extra: String = "") {
        viewModelScope.launch { current.value?.admin(type, arg, extra) }
    }

    fun sendImage(bytes: ByteArray, name: String) {
        val client = current.value ?: return
        viewModelScope.launch(Dispatchers.IO) {
            val ext = name.substringAfterLast('.', "png").lowercase().take(4)
            client.sendMedia(bytes, "img", ext, name)
        }
    }

    fun sendFile(bytes: ByteArray, name: String, kind: String) {
        val client = current.value ?: return
        viewModelScope.launch(Dispatchers.IO) {
            val ext = name.substringAfterLast('.', if (kind == "aud") "mp3" else "mp4")
            client.sendMedia(bytes, kind, ext.lowercase().take(4), name)
        }
    }

    private var imagePicker: (() -> Unit)? = null
    private var mediaPicker: (() -> Unit)? = null

    fun attachPickers(image: () -> Unit, media: () -> Unit) {
        imagePicker = image
        mediaPicker = media
    }

    fun pickImage() { imagePicker?.invoke() }

    fun pickMedia() { mediaPicker?.invoke() }

    fun setSetting(key: String, value: Any) {
        store.saveSetting(key, value)
        settingsVersion.value += 1
    }

    val settingsVersion = MutableStateFlow(0)

    fun setting(key: String, fallback: Boolean): Boolean = store.bool(key, fallback)

    override fun onCleared() {
        current.value?.disconnect()
        super.onCleared()
    }
}
