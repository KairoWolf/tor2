package org.tor2.chat

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import java.io.File

/** Hands a downloaded file to whatever app the phone uses for it. */
object OpenMedia {

    fun open(context: Context, file: File): Boolean {
        if (!file.isFile) return false
        val uri = runCatching {
            FileProvider.getUriForFile(context, "${context.packageName}.files", file)
        }.getOrNull() ?: return false
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, mimeFor(file.name))
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or
                     Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        return runCatching { context.startActivity(intent); true }.getOrDefault(false)
    }

    private fun mimeFor(name: String): String = when (name.substringAfterLast('.', "")
        .lowercase()) {
        "mp4", "m4v" -> "video/mp4"
        "webm" -> "video/webm"
        "mkv" -> "video/x-matroska"
        "mp3" -> "audio/mpeg"
        "m4a", "aac" -> "audio/mp4"
        "ogg", "opus" -> "audio/ogg"
        "wav" -> "audio/wav"
        "png" -> "image/png"
        "jpg", "jpeg" -> "image/jpeg"
        "gif" -> "image/gif"
        "webp" -> "image/webp"
        else -> "*/*"
    }
}
