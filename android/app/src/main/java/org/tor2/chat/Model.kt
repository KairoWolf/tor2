package org.tor2.chat

import androidx.compose.runtime.Immutable
import org.json.JSONObject

@Immutable
data class MediaInfo(
    val id: Int,
    val kind: String,          // img | vid | aud
    val name: String,
    val size: Long,
    val ext: String,
    val thumb: ByteArray? = null,
) {
    val display: String get() = name.ifBlank {
        when (kind) { "img" -> "image"; "aud" -> "audio"; else -> "video" }
    }

    override fun equals(other: Any?) = other is MediaInfo && other.id == id
    override fun hashCode() = id
}

@Immutable
data class Message(
    val id: Int?,
    val channel: String,
    val nick: String,
    val timestamp: Long,
    val body: String?,
    val media: MediaInfo? = null,
    val mine: Boolean = false,
    val mentioned: Boolean = false,
    val pending: Boolean = false,       // drawn locally, not yet confirmed
    val inlineImage: ByteArray? = null,
) {
    override fun equals(other: Any?) = other is Message &&
            other.id == id && other.body == body && other.pending == pending
    override fun hashCode() = (id ?: 0) * 31 + (body?.hashCode() ?: 0)
}

@Immutable
data class Channel(
    val name: String,
    val unread: Int = 0,
    val mentioned: Boolean = false,
)

/** A saved server, whether or not it is currently connected. */
@Immutable
data class SavedServer(
    val key: String,
    val onion: String,
    val token: String,
    val displayName: String,
)

@Immutable
data class Contact(val name: String, val onion: String)

enum class ConnState { Idle, Connecting, WaitingToBeAccepted, Incoming, Connected, Failed }

@Immutable
data class TransferState(
    val label: String = "",
    val done: Long = 0,
    val total: Long = 0,
    val active: Boolean = false,
) {
    val fraction: Float get() = if (total <= 0) 0f else (done.toFloat() / total)
}

fun mediaFrom(json: JSONObject?): MediaInfo? {
    if (json == null) return null
    return MediaInfo(
        id = json.optInt("id", -1),
        kind = json.optString("kind", "vid"),
        name = json.optString("name", ""),
        size = json.optLong("size", 0),
        ext = json.optString("ext", "bin"),
        thumb = json.optString("thumb", "").takeIf { it.isNotEmpty() }
            ?.let { android.util.Base64.decode(it, android.util.Base64.DEFAULT) },
    )
}
