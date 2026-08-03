package org.tor2.chat

import android.graphics.Bitmap
import android.media.MediaMetadataRetriever
import java.io.ByteArrayOutputStream
import java.io.File

/** Stills and metadata for video and audio, using Android's own decoders. */
object Media {

    const val MAX_THUMB = 120 * 1024

    /**
     * A JPEG still from a video, so people can see what something is before
     * spending minutes downloading it over Tor.
     */
    fun thumbnail(file: File): ByteArray? = runCatching {
        MediaMetadataRetriever().use { r ->
            r.setDataSource(file.absolutePath)
            val frame = r.getFrameAtTime(1_000_000) ?: r.getFrameAtTime(0)
            frame?.let { scaleAndCompress(it) }
        }
    }.getOrNull()

    fun durationSeconds(file: File): Long = runCatching {
        MediaMetadataRetriever().use { r ->
            r.setDataSource(file.absolutePath)
            (r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                ?.toLongOrNull() ?: 0L) / 1000
        }
    }.getOrDefault(0L)

    private fun scaleAndCompress(bitmap: Bitmap): ByteArray? {
        val width = 480
        val scaled = if (bitmap.width > width) {
            val height = (bitmap.height * width / bitmap.width.toFloat()).toInt()
            Bitmap.createScaledBitmap(bitmap, width, maxOf(1, height), true)
        } else bitmap
        for (quality in intArrayOf(70, 55, 40)) {
            val out = ByteArrayOutputStream()
            scaled.compress(Bitmap.CompressFormat.JPEG, quality, out)
            if (out.size() <= MAX_THUMB) return out.toByteArray()
        }
        return null
    }

    /** Shrink an oversized picture so it fits the 5 MB image limit. */
    fun shrinkImage(bytes: ByteArray, limit: Int): ByteArray {
        if (bytes.size <= limit) return bytes
        val bitmap = android.graphics.BitmapFactory.decodeByteArray(
            bytes, 0, bytes.size) ?: return bytes
        var quality = 85
        var out = ByteArrayOutputStream()
        while (quality >= 40) {
            out = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.JPEG, quality, out)
            if (out.size() <= limit) break
            quality -= 15
        }
        return out.toByteArray()
    }
}
