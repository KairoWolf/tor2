package org.tor2.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.slideInVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.derivedStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private val clock = SimpleDateFormat("HH:mm", Locale.getDefault())

/** The message list, composer and everything that animates in between. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatPane(
    vm: AppViewModel,
    client: ServerClient,
    onOpenChannels: () -> Unit,
    onOpenMembers: () -> Unit,
    onOpenAdmin: () -> Unit = {},
) {
    val channels by client.channels.collectAsState()
    val active by client.active.collectAsState()
    val byChannel by client.messages.collectAsState()
    val online by client.online.collectAsState()
    val transfer by client.transfer.collectAsState()
    val notice by client.notice.collectAsState()
    val state by client.state.collectAsState()
    val isAdmin by client.isAdmin.collectAsState()
    val messages = byChannel[active].orEmpty()
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()

    // follow new messages, but only when already at the bottom, so reading
    // history is never yanked away
    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty() &&
            listState.firstVisibleItemIndex >= messages.size - 4) {
            scope.launch { listState.animateScrollToItem(messages.lastIndex) }
        }
    }

    Column(Modifier.fillMaxSize()) {
        ChannelHeader(
            channel = active,
            online = online.size,
            connected = state == ConnState.Connected,
            isAdmin = isAdmin,
            onChannels = onOpenChannels,
            onMembers = onOpenMembers,
            onAdmin = onOpenAdmin,
        )

        AnimatedVisibility(
            visible = notice != null,
            enter = expandVertically(spring(Spring.DampingRatioMediumBouncy)) + fadeIn(),
            exit = shrinkVertically() + fadeOut(),
        ) {
            NoticeStrip(notice.orEmpty()) { client.notice.value = null }
        }

        AnimatedVisibility(transfer.active, enter = expandVertically() + fadeIn(),
                           exit = shrinkVertically() + fadeOut()) {
            TransferStrip(transfer)
        }

        val focus = LocalFocusManager.current
        val keyboard = LocalSoftwareKeyboardController.current
        // reading should not mean fighting the keyboard: a tap or a scroll
        // puts it away
        LaunchedEffect(listState) {
            snapshotFlow { listState.isScrollInProgress }.collect { scrolling ->
                if (scrolling) { focus.clearFocus(); keyboard?.hide() }
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth()
                .pointerInput(Unit) {
                    detectTapGestures { focus.clearFocus(); keyboard?.hide() }
                }) {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 10.dp),
            ) {
                item {
                    TextButton(onClick = { vm.loadOlder() },
                               modifier = Modifier.fillMaxWidth()) {
                        Text("Load earlier messages",
                             style = MaterialTheme.typography.labelSmall)
                    }
                }
                itemsIndexed(messages, key = { _, m -> m.id ?: m.hashCode() }) { i, msg ->
                    val previous = messages.getOrNull(i - 1)
                    if (previous == null || !sameDay(previous.timestamp, msg.timestamp)) {
                        DaySeparator(msg.timestamp)
                    }
                    MessageRow(
                        msg = msg,
                        grouped = previous != null &&
                                sameDay(previous.timestamp, msg.timestamp) &&
                                previous.nick == msg.nick &&
                                msg.timestamp - previous.timestamp < 5 * 60_000,
                        onPreview = { vm.preview(it) },
                        onDownload = { vm.download(it) },
                        onDelete = { vm.deleteMessage(it) },
                        onOpen = { vm.openMedia(it) },
                    )
                }
                item { Spacer(Modifier.height(4.dp)) }
            }

            // only offered when it would do something: reading history
            val awayFromBottom by remember {
                derivedStateOf {
                    messages.isNotEmpty() &&
                        listState.firstVisibleItemIndex < messages.size - 3
                }
            }
            androidx.compose.animation.AnimatedVisibility(
                visible = awayFromBottom,
                modifier = Modifier.align(Alignment.BottomEnd).padding(14.dp),
                enter = scaleIn(spring(Spring.DampingRatioMediumBouncy)) + fadeIn(),
                exit = scaleOut() + fadeOut(),
            ) {
                SmallFloatingActionButton(
                    onClick = {
                        scope.launch { listState.animateScrollToItem(messages.lastIndex) }
                    },
                ) { Icon(Icons.Default.KeyboardArrowDown, "jump to latest") }
            }

            androidx.compose.animation.AnimatedVisibility(
                visible = messages.isEmpty(),
                modifier = Modifier.align(Alignment.Center),
                enter = fadeIn(), exit = fadeOut(),
            ) {
                EmptyChannel(active)
            }
        }

        Composer(
            enabled = state == ConnState.Connected,
            onSend = { vm.send(it) },
            onPicture = { vm.pickImage() },
            onVideo = { vm.pickMedia() },
            onAudio = { vm.pickAudio() },
        )
    }
}

@Composable
private fun ChannelHeader(
    channel: String, online: Int, connected: Boolean, isAdmin: Boolean,
    onChannels: () -> Unit, onMembers: () -> Unit, onAdmin: () -> Unit,
) {
    Surface(tonalElevation = 3.dp, shadowElevation = 2.dp) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onChannels) {
                Icon(Icons.Default.Menu, "channels")
            }
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("#", color = MaterialTheme.colorScheme.primary,
                         fontWeight = FontWeight.Bold, fontSize = 18.sp)
                    Spacer(Modifier.width(2.dp))
                    Text(channel, style = MaterialTheme.typography.titleMedium,
                         maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                ConnectionDot(connected, online)
            }
            if (isAdmin) {
                IconButton(onClick = onAdmin) {
                    Icon(Icons.Default.AdminPanelSettings, "manage server")
                }
            }
            IconButton(onClick = onMembers) {
                Icon(Icons.Default.People, "members")
            }
        }
    }
}

@Composable
private fun ConnectionDot(connected: Boolean, online: Int) {
    val alpha by animateFloatAsState(if (connected) 1f else 0.45f,
                                     tween(400), label = "dot")
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier.size(7.dp).clip(CircleShape).background(
                if (connected) MaterialTheme.colorScheme.secondary
                else MaterialTheme.colorScheme.error.copy(alpha = alpha))
        )
        Spacer(Modifier.width(6.dp))
        Text(
            if (connected) "$online online" else "reconnecting…",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun NoticeStrip(text: String, onDismiss: () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.primaryContainer,
            modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text(text, Modifier.weight(1f),
                 style = MaterialTheme.typography.bodyMedium,
                 color = MaterialTheme.colorScheme.onPrimaryContainer)
            IconButton(onClick = onDismiss, modifier = Modifier.size(22.dp)) {
                Icon(Icons.Default.Close, "dismiss",
                     tint = MaterialTheme.colorScheme.onPrimaryContainer)
            }
        }
    }
}

@Composable
private fun TransferStrip(t: TransferState) {
    val progress by animateFloatAsState(t.fraction, tween(250), label = "xfer")
    Surface(color = MaterialTheme.colorScheme.surfaceVariant) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(t.label, Modifier.weight(1f),
                     style = MaterialTheme.typography.labelSmall,
                     maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text("${(t.fraction * 100).toInt()}%",
                     style = MaterialTheme.typography.labelSmall,
                     color = MaterialTheme.colorScheme.primary)
            }
            Spacer(Modifier.height(5.dp))
            LinearProgressIndicator(progress = { progress },
                                    modifier = Modifier.fillMaxWidth().height(5.dp)
                                        .clip(RoundedCornerShape(3.dp)))
            Spacer(Modifier.height(4.dp))
            Text("${fmtSize(t.done)} of ${fmtSize(t.total)}",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** A quiet marker between days, so a long scroll stays legible. */
@Composable
private fun DaySeparator(timestamp: Long) {
    val label = remember(timestamp / 86_400_000) { dayLabel(timestamp) }
    Row(Modifier.fillMaxWidth().padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically) {
        HorizontalDivider(Modifier.weight(1f),
                          color = MaterialTheme.colorScheme.outline.copy(alpha = 0.4f))
        Text(label, Modifier.padding(horizontal = 10.dp),
             style = MaterialTheme.typography.labelSmall,
             color = MaterialTheme.colorScheme.onSurfaceVariant)
        HorizontalDivider(Modifier.weight(1f),
                          color = MaterialTheme.colorScheme.outline.copy(alpha = 0.4f))
    }
}

private fun sameDay(a: Long, b: Long): Boolean {
    val ca = java.util.Calendar.getInstance().apply { timeInMillis = a }
    val cb = java.util.Calendar.getInstance().apply { timeInMillis = b }
    return ca.get(java.util.Calendar.YEAR) == cb.get(java.util.Calendar.YEAR) &&
            ca.get(java.util.Calendar.DAY_OF_YEAR) == cb.get(java.util.Calendar.DAY_OF_YEAR)
}

private fun dayLabel(timestamp: Long): String {
    val now = System.currentTimeMillis()
    return when {
        sameDay(timestamp, now) -> "Today"
        sameDay(timestamp, now - 86_400_000) -> "Yesterday"
        else -> SimpleDateFormat("d MMMM", Locale.getDefault()).format(Date(timestamp))
    }
}

@Composable
private fun EmptyChannel(channel: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Icon(Icons.Default.ChatBubbleOutline, null, Modifier.size(52.dp),
             tint = MaterialTheme.colorScheme.primary.copy(alpha = 0.45f))
        Spacer(Modifier.height(12.dp))
        Text("#$channel is quiet", style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(4.dp))
        Text("Say something, or tap + to share a picture, video or song",
             style = MaterialTheme.typography.bodyMedium,
             color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** One message: grouped under the previous when the same person just spoke. */
@Composable
fun MessageRow(
    msg: Message,
    grouped: Boolean,
    onPreview: (Int) -> Unit,
    onDownload: (Int) -> Unit,
    onDelete: (Int) -> Unit,
    onOpen: (MediaInfo) -> Unit,
) {
    var showActions by remember { mutableStateOf(false) }
    val enter = remember {
        fadeIn(tween(220)) + slideInVertically(spring(Spring.DampingRatioLowBouncy)) { it / 3 }
    }
    AnimatedVisibility(visible = true, enter = enter) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(top = if (grouped) 1.dp else 9.dp)
                .clip(RoundedCornerShape(10.dp))
                .then(
                    if (msg.mentioned)
                        Modifier.background(
                            MaterialTheme.colorScheme.tertiary.copy(alpha = 0.12f))
                    else Modifier
                )
                .clickable { showActions = !showActions }
                .padding(horizontal = 8.dp, vertical = 3.dp),
        ) {
            if (!grouped) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Avatar(msg.nick)
                    Spacer(Modifier.width(8.dp))
                    val hue = remember(msg.nick) {
                        (msg.nick.hashCode().toFloat() % 360f + 360f) % 360f
                    }
                    Text(msg.nick, style = MaterialTheme.typography.titleMedium,
                         color = if (msg.mine) MaterialTheme.colorScheme.primary
                                 else Color.hsl(hue, 0.5f, 0.72f))
                    Spacer(Modifier.width(8.dp))
                    Text(clock.format(Date(msg.timestamp)),
                         style = MaterialTheme.typography.labelSmall,
                         color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (msg.pending) {
                        Spacer(Modifier.width(6.dp))
                        Icon(Icons.Default.Schedule, "sending",
                             Modifier.size(12.dp),
                             tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            msg.body?.takeIf { it.isNotBlank() }?.let {
                Text(it, Modifier.padding(start = 40.dp, top = 2.dp, end = 4.dp),
                     style = MaterialTheme.typography.bodyLarge,
                     color = if (msg.pending)
                         MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                     else MaterialTheme.colorScheme.onSurface)
            }
            msg.inlineImage?.let { InlineImage(it) }
            // A still travels with every picture and video, so the chat shows
            // what something is without anyone tapping download first.
            msg.media?.takeIf { msg.inlineImage == null }
                ?.let { MediaCard(it, onPreview, onDownload, onOpen) }

            AnimatedVisibility(showActions && msg.id != null,
                               enter = expandVertically() + fadeIn(),
                               exit = shrinkVertically() + fadeOut()) {
                val clipboard = androidx.compose.ui.platform.LocalClipboardManager.current
                Row(Modifier.padding(start = 40.dp, top = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    msg.body?.takeIf { it.isNotBlank() }?.let { body ->
                        AssistChip(
                            onClick = {
                                clipboard.setText(
                                    androidx.compose.ui.text.AnnotatedString(body))
                                showActions = false
                            },
                            label = { Text("Copy") },
                            leadingIcon = {
                                Icon(Icons.Default.ContentCopy, null,
                                     Modifier.size(16.dp))
                            })
                    }
                    AssistChip(onClick = { msg.id?.let(onDelete); showActions = false },
                               label = { Text("Delete") },
                               leadingIcon = {
                                   Icon(Icons.Default.DeleteOutline, null,
                                        Modifier.size(16.dp))
                               })
                }
            }
        }
    }
}

@Composable
private fun Avatar(nick: String) {
    // a stable colour per person, so the eye can track who is speaking
    val hue = remember(nick) { (nick.hashCode().toFloat() % 360f + 360f) % 360f }
    val color = remember(hue) { Color.hsl(hue, 0.5f, 0.62f) }
    Box(
        Modifier.size(32.dp).clip(CircleShape).background(color.copy(alpha = 0.25f))
            .border(1.dp, color.copy(alpha = 0.6f), CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Text(nick.take(1).uppercase(), color = color,
             fontWeight = FontWeight.Bold, fontSize = 14.sp)
    }
}

@Composable
private fun InlineImage(bytes: ByteArray) {
    val bitmap = remember(bytes) {
        runCatching {
            android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        }.getOrNull()
    }
    bitmap?.let {
        androidx.compose.foundation.Image(
            bitmap = it.asImageBitmap(),
            contentDescription = "image",
            contentScale = ContentScale.Fit,
            modifier = Modifier
                .padding(start = 40.dp, top = 6.dp)
                .heightIn(max = 260.dp)
                .clip(RoundedCornerShape(12.dp)),
        )
    }
}

@Composable
private fun MediaCard(info: MediaInfo, onPreview: (Int) -> Unit,
                      onDownload: (Int) -> Unit, onOpen: (MediaInfo) -> Unit) {
    val icon = when (info.kind) {
        "aud" -> Icons.Default.MusicNote
        "img" -> Icons.Default.Image
        else -> Icons.Default.Movie
    }
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.padding(start = 40.dp, top = 6.dp).fillMaxWidth(0.92f),
    ) {
        Column(Modifier.padding(10.dp)) {
            info.thumb?.let {
                InlineThumb(it, info.kind != "img") { onOpen(info) }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(icon, null, tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(info.display, style = MaterialTheme.typography.bodyMedium,
                         maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(fmtSize(info.size), style = MaterialTheme.typography.labelSmall,
                         color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (info.kind == "vid" || info.kind == "aud") {
                    FilledTonalIconButton(onClick = { onOpen(info) }) {
                        Icon(Icons.Default.PlayArrow, "play")
                    }
                    Spacer(Modifier.width(6.dp))
                }
                IconButton(onClick = { onDownload(info.id) }) {
                    Icon(Icons.Default.Download, "save")
                }
            }
        }
    }
}

@Composable
private fun InlineThumb(bytes: ByteArray, isVideo: Boolean, onPlay: () -> Unit) {
    val bitmap = remember(bytes) {
        runCatching {
            android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        }.getOrNull()
    }
    bitmap?.let {
        Box(contentAlignment = Alignment.Center,
            modifier = Modifier.clickable(enabled = isVideo) { onPlay() }) {
            androidx.compose.foundation.Image(
                bitmap = it.asImageBitmap(),
                contentDescription = "preview",
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxWidth().heightIn(max = 220.dp)
                    .clip(RoundedCornerShape(8.dp)),
            )
            if (isVideo) {
                Box(Modifier.size(52.dp).clip(CircleShape)
                        .background(Color.Black.copy(alpha = 0.55f)),
                    contentAlignment = Alignment.Center) {
                    Icon(Icons.Default.PlayArrow, "play", tint = Color.White,
                         modifier = Modifier.size(30.dp))
                }
            }
        }
        Spacer(Modifier.height(8.dp))
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Composer(enabled: Boolean, onSend: (String) -> Unit,
                     onPicture: () -> Unit, onVideo: () -> Unit,
                     onAudio: () -> Unit) {
    var text by remember { mutableStateOf("") }
    var showAttach by remember { mutableStateOf(false) }
    val canSend = text.isNotBlank() && enabled
    Surface(tonalElevation = 3.dp) {
      Column(Modifier.windowInsetsPadding(
                 WindowInsets.ime.union(WindowInsets.navigationBars))) {
        AnimatedVisibility(showAttach, enter = expandVertically() + fadeIn(),
                           exit = shrinkVertically() + fadeOut()) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AssistChip(onClick = { showAttach = false; onPicture() },
                           label = { Text("Picture") },
                           leadingIcon = { Icon(Icons.Default.Image, null,
                                                Modifier.size(18.dp)) })
                AssistChip(onClick = { showAttach = false; onVideo() },
                           label = { Text("Video") },
                           leadingIcon = { Icon(Icons.Default.Movie, null,
                                                Modifier.size(18.dp)) })
                AssistChip(onClick = { showAttach = false; onAudio() },
                           label = { Text("Audio") },
                           leadingIcon = { Icon(Icons.Default.MusicNote, null,
                                                Modifier.size(18.dp)) })
            }
        }
        Row(
            Modifier.fillMaxWidth().padding(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = { showAttach = !showAttach }, enabled = enabled) {
                Icon(if (showAttach) Icons.Default.Close
                     else Icons.Default.AddPhotoAlternate, "attach")
            }
            TextField(
                value = text,
                onValueChange = { text = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text(if (enabled) "Message" else "Connecting…") },
                enabled = enabled,
                maxLines = 5,
                shape = RoundedCornerShape(22.dp),
                colors = TextFieldDefaults.colors(
                    focusedIndicatorColor = Color.Transparent,
                    unfocusedIndicatorColor = Color.Transparent,
                    disabledIndicatorColor = Color.Transparent,
                ),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = {
                    if (canSend) { onSend(text.trim()); text = "" }
                }),
            )
            Spacer(Modifier.width(6.dp))
            val scale by animateFloatAsState(if (canSend) 1f else 0.8f,
                                             spring(Spring.DampingRatioMediumBouncy),
                                             label = "send")
            FilledIconButton(
                onClick = { if (canSend) { onSend(text.trim()); text = "" } },
                enabled = canSend,
                modifier = Modifier.size((46 * scale).dp),
            ) {
                Icon(Icons.AutoMirrored.Filled.Send, "send")
            }
        }
      }
    }
}

fun fmtSize(n: Long): String = when {
    n < 1024 -> "$n B"
    n < 1024 * 1024 -> "${n / 1024} KB"
    else -> String.format(Locale.US, "%.1f MB", n / 1024.0 / 1024.0)
}
