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
    val myAddress = tor.myOnion

    // direct chats run alongside servers; [openChat] is the one on screen
    val chats = MutableStateFlow<List<DirectChat>>(emptyList())
    val openChat = MutableStateFlow<DirectChat?>(null)
    val incomingRequest = MutableStateFlow<DirectChat?>(null)
    private var inbound: InboundServer? = null

    init {
        viewModelScope.launch {
            runCatching { tor.awaitReady() }
                .onSuccess {
                    torReady.value = true
                    banner.value = null
                    startListening()
                    savedServers.value.firstOrNull()?.let { open(it) }
                }
                .onFailure { banner.value = "Tor could not start: ${it.message}" }
        }
    }

    /**
     * Publish this phone's own onion address and listen behind it, so people
     * can start a direct chat with it. The key is kept, so the address a
     * friend saved keeps working.
     */
    private var listening = false

    private fun startListening() {
        if (listening) return
        listening = true
        val server = InboundServer(viewModelScope) { session, hello ->
            val nickname = hello.optString("nick", "peer").take(32)
            val chat = DirectChat(tor, viewModelScope, store, nick.value,
                                  "in:${System.currentTimeMillis()}", "",
                                  incoming = session)
            chat.adoptIncoming(hello)
            chats.value = chats.value + chat
            incomingRequest.value = chat
        }
        inbound = server
        server.start()
        viewModelScope.launch {
            val published = tor.publishOnion(server.port, store.onionKey)
            if (published == null) {
                banner.value = "could not publish an address — others cannot " +
                        "start a chat with you, but you can still start one"
            } else {
                store.onionKey = published.second
            }
        }
    }

    fun startChat(address: String, label: String) {
        val onion = address.trim().lowercase()
            .removePrefix("http://").removePrefix("https://").trimEnd('/')
            .removeSuffix(".onion") + ".onion"
        if (!Regex("^[a-z2-7]{56}\\.onion$").matches(onion)) {
            banner.value = "that does not look like a v3 onion address"
            return
        }
        if (label.isNotBlank()) store.saveContact(Contact(label, onion))
        contacts.value = store.contacts()
        val chat = DirectChat(tor, viewModelScope, store, nick.value,
                              "out:$onion", onion)
        chats.value = chats.value + chat
        openChat.value = chat
        current.value = null
        viewModelScope.launch { chat.connect() }
    }

    fun openDirect(chat: DirectChat) {
        chat.markRead()
        openChat.value = chat
        current.value = null
    }

    fun acceptIncoming() {
        val chat = incomingRequest.value ?: return
        incomingRequest.value = null
        viewModelScope.launch {
            chat.accept()
            openDirect(chat)
        }
    }

    fun rejectIncoming() {
        incomingRequest.value?.let { chat ->
            chat.reject()
            chats.value = chats.value - chat
        }
        incomingRequest.value = null
    }

    fun closeChat(chat: DirectChat) {
        chat.disconnect()
        chats.value = chats.value - chat
        if (openChat.value === chat) openChat.value = null
    }

    fun sendDirect(text: String) {
        val chat = openChat.value ?: return
        viewModelScope.launch { chat.send(text) }
    }

    /** Called once the Tor service is bound and its control port is usable. */
    fun onTorServiceReady() {
        if (torReady.value && tor.myOnion.value == null) startListening()
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
        openChat.value = null
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
        val chat = openChat.value
        val client = current.value
        viewModelScope.launch(Dispatchers.IO) {
            val sized = Media.shrinkImage(bytes, Protocol.MAX_IMAGE)
            val ext = name.substringAfterLast('.', "png").lowercase().take(4)
            when {
                chat != null -> chat.sendImage(sized, name)
                client != null -> client.sendMedia(sized, "img", ext, name)
            }
        }
    }

    /** Video and audio: a still is attached so nobody downloads blind. */
    fun sendFile(bytes: ByteArray, name: String, kind: String) {
        val chat = openChat.value
        val client = current.value
        viewModelScope.launch(Dispatchers.IO) {
            val ext = name.substringAfterLast('.', if (kind == "aud") "mp3" else "mp4")
                .lowercase().take(4)
            val temp = File(store.mediaDir, "outgoing_${System.currentTimeMillis()}.$ext")
            temp.writeBytes(bytes)
            val thumb = if (kind == "vid") Media.thumbnail(temp) else null
            try {
                when {
                    chat != null -> chat.sendFile(temp, kind == "aud")
                    client != null -> client.sendMedia(bytes, kind, ext, name, thumb)
                }
            } finally {
                temp.delete()
            }
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
        inbound?.stop()
        tor.unpublishOnion()
        chats.value.forEach { it.disconnect() }
        current.value?.disconnect()
        super.onCleared()
    }
}
