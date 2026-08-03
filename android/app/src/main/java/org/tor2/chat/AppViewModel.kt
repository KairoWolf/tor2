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
            var published = tor.publishOnion(server.port, store.onionKey)
            var attempt = 0
            while (published == null && attempt < 3) {   // tor can be slow to settle
                attempt += 1
                kotlinx.coroutines.delay(10_000)
                published = tor.publishOnion(server.port, store.onionKey)
            }
            if (published == null) {
                addressError.value = "Could not publish an address. You can still " +
                        "start chats and join servers; others just cannot start " +
                        "a chat with you until this works."
            } else {
                store.onionKey = published.second
                addressError.value = null
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

    /** Join with eight digits and nothing else. */
    fun joinWithCode(code: String, label: String = "") {
        val digits = code.filter { it.isDigit() }
        if (digits.length != 8) {
            banner.value = "A server code is 8 digits"
            return
        }
        val derived = runCatching { Codes.serverAddress(digits) }
        val onion = derived.getOrNull()
        if (onion == null) {
            val why = derived.exceptionOrNull()
            android.util.Log.e("tor2", "could not derive address for $digits", why)
            banner.value = "Could not work out an address for that code" +
                    (why?.message?.let { ": $it" } ?: "")
            return
        }
        android.util.Log.i("tor2", "code $digits -> ${onion.take(16)}…")
        val key = label.ifBlank { "server-$digits" }
        val saved = SavedServer(key, onion, "", label.ifBlank { "Server $digits" })
        store.saveServer(saved)
        savedServers.value = store.servers()
        openChat.value = null
        current.value?.disconnect()
        val client = ServerClient(tor, viewModelScope, store, nick.value, saved)
        current.value = client
        watchDownloads(client)
        viewModelScope.launch { client.connect(joinCode = digits) }
    }

    /** Pair a direct chat with five digits. */
    fun chatWithCode(code: String) {
        val digits = code.filter { it.isDigit() }
        if (digits.length != 5) {
            banner.value = "A chat code is 5 digits"
            return
        }
        val onion = runCatching { Codes.chatAddress(digits) }.getOrNull()
        if (onion == null) {
            banner.value = "Could not work out an address for that code"
            return
        }
        startChat(onion, "")
    }

    /** Publish a 5-digit code others can use to reach this phone. */
    fun createChatCode() {
        val server = inbound ?: return
        val digits = "%05d".format(java.security.SecureRandom().nextInt(100_000))
        viewModelScope.launch {
            val addr = tor.publishCodeOnion(digits, server.port, Codes.CHAT_PERSON)
            if (addr == null) {
                banner.value = "Could not publish a code"
            } else {
                pairingCode.value = digits
                banner.value = null
            }
        }
    }

    val pairingCode = MutableStateFlow<String?>(null)
    val addressError = MutableStateFlow<String?>(null)

    /** Try publishing our address again, after a failure. */
    fun retryAddress() {
        addressError.value = null
        listening = false
        startListening()
    }

    fun clearChatCode() {
        pairingCode.value?.let { code ->
            viewModelScope.launch { tor.unpublishCode(code) }
        }
        pairingCode.value = null
    }

    fun open(saved: SavedServer) = connect(saved, "")

    private fun connect(saved: SavedServer, invite: String) {
        openChat.value = null
        current.value?.disconnect()
        val client = ServerClient(tor, viewModelScope, store, nick.value, saved)
        current.value = client
        watchDownloads(client)
        viewModelScope.launch { client.connect(invite) }
    }

    private fun watchDownloads(client: ServerClient) {
        viewModelScope.launch {
            client.downloadReady.collect { file ->
                if (file != null) {
                    onDownloadFinished(file)
                    client.downloadReady.value = null
                }
            }
        }
    }

    fun leave(saved: SavedServer) {
        if (current.value?.saved?.key == saved.key) {
            current.value?.disconnect()
            current.value = null
        }
        store.removeServer(saved.key)
        savedServers.value = store.servers()
        banner.value = "Left ${saved.displayName}"
        // show whatever is left, so the screen is never blank for no reason
        savedServers.value.firstOrNull()?.let { open(it) }
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

    /**
     * Play something. If it is already on the phone it opens straight away,
     * otherwise it downloads first and opens when it lands — tapping play
     * should play, not silently queue a transfer.
     */
    fun openMedia(info: MediaInfo) {
        val existing = findDownloaded(info)
        if (existing != null) {
            playing.value = existing to info.display
            return
        }
        val client = current.value ?: return
        banner.value = "Fetching ${info.display}…"
        pendingOpen = info.id
        pendingTitle = info.display
        viewModelScope.launch { client.fetch(info.id) }
    }

    /** The file currently open in the in-app player, with its title. */
    val playing = MutableStateFlow<Pair<File, String>?>(null)

    fun closePlayer() { playing.value = null }

    private var pendingTitle: String = ""

    private var pendingOpen: Int? = null

    private fun findDownloaded(info: MediaInfo): File? {
        val dir = File(store.mediaDir, "received")
        if (!dir.isDirectory) return null
        return dir.listFiles()?.firstOrNull { it.name.startsWith("media_${info.id}.") }
    }

    /** Called when a download finishes, so a tapped video opens by itself. */
    private fun onDownloadFinished(file: File) {
        val wanted = pendingOpen ?: return
        if (!file.name.startsWith("media_$wanted.")) return
        pendingOpen = null
        banner.value = null
        playing.value = file to pendingTitle.ifBlank { file.name }
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
    private var audioPicker: (() -> Unit)? = null

    fun attachPickers(image: () -> Unit, media: () -> Unit, audio: () -> Unit) {
        imagePicker = image
        mediaPicker = media
        audioPicker = audio
    }

    fun pickImage() { imagePicker?.invoke() }

    fun pickMedia() { mediaPicker?.invoke() }

    fun pickAudio() { audioPicker?.invoke() }

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
