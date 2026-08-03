package org.tor2.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import java.io.File

/**
 * Watches or listens to something inside the app.
 *
 * Media stays in the app's private storage and is never handed to another
 * app — nothing lands in the gallery or the music library unless you choose
 * to save it.
 */
@Composable
fun PlayerOverlay(file: File?, title: String, onClose: () -> Unit) {
    val context = LocalContext.current

    AnimatedVisibility(visible = file != null, enter = fadeIn(), exit = fadeOut()) {
        val target = file ?: return@AnimatedVisibility
        val player = remember(target) {
            ExoPlayer.Builder(context).build().apply {
                setMediaItem(MediaItem.fromUri(target.toURI().toString()))
                prepare()
                playWhenReady = true
            }
        }
        DisposableEffect(target) {
            onDispose { player.release() }
        }

        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.94f))) {
            AndroidView(
                factory = { ctx ->
                    PlayerView(ctx).apply {
                        this.player = player
                        useController = true
                        setShowNextButton(false)
                        setShowPreviousButton(false)
                    }
                },
                modifier = Modifier.fillMaxWidth().align(Alignment.Center)
                    .heightIn(min = 220.dp),
            )
            Row(
                Modifier.fillMaxWidth().align(Alignment.TopStart)
                    .statusBarsPadding().padding(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = { player.pause(); onClose() }) {
                    Icon(Icons.Default.Close, "close", tint = Color.White)
                }
                Text(title, color = Color.White,
                     style = MaterialTheme.typography.titleMedium)
            }
        }
    }
}
