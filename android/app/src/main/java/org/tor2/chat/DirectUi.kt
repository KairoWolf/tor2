package org.tor2.chat

import androidx.compose.animation.*
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private val clock = SimpleDateFormat("HH:mm", Locale.getDefault())

/** A one-to-one conversation: no server, nobody else can read it. */
@Composable
fun DirectPane(vm: AppViewModel, chat: DirectChat, onBack: () -> Unit) {
    val state by chat.state.collectAsState()
    val peer by chat.peerNick.collectAsState()
    val msgs by chat.messages.collectAsState()
    val notice by chat.notice.collectAsState()
    val fp by chat.fingerprint.collectAsState()
    val transfer by chat.transfer.collectAsState()
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    var showFingerprint by remember { mutableStateOf(false) }

    LaunchedEffect(msgs.size) {
        if (msgs.isNotEmpty()) scope.launch { listState.animateScrollToItem(msgs.lastIndex) }
    }

    Column(Modifier.fillMaxSize()) {
        Surface(tonalElevation = 3.dp, shadowElevation = 2.dp) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 6.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, "back")
                }
                Column(Modifier.weight(1f)) {
                    Text(peer, style = MaterialTheme.typography.titleMedium,
                         maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(
                        when (state) {
                            ConnState.Connected -> "end-to-end encrypted"
                            ConnState.WaitingToBeAccepted -> "waiting to be accepted…"
                            ConnState.Connecting -> "connecting through Tor…"
                            ConnState.Incoming -> "wants to chat"
                            ConnState.Failed -> "could not connect"
                            else -> "not connected"
                        },
                        style = MaterialTheme.typography.labelSmall,
                        color = if (state == ConnState.Connected)
                            MaterialTheme.colorScheme.secondary
                        else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                IconButton(onClick = { showFingerprint = true }) {
                    Icon(Icons.Default.VerifiedUser, "verify")
                }
            }
        }

        AnimatedVisibility(notice != null, enter = expandVertically() + fadeIn(),
                           exit = shrinkVertically() + fadeOut()) {
            Surface(color = MaterialTheme.colorScheme.primaryContainer,
                    modifier = Modifier.fillMaxWidth()) {
                Text(notice.orEmpty(), Modifier.padding(14.dp, 8.dp),
                     style = MaterialTheme.typography.bodyMedium,
                     color = MaterialTheme.colorScheme.onPrimaryContainer)
            }
        }

        AnimatedVisibility(transfer.active, enter = expandVertically() + fadeIn(),
                           exit = shrinkVertically() + fadeOut()) {
            Column(Modifier.fillMaxWidth().padding(14.dp, 6.dp)) {
                Text("${transfer.label} · ${fmtSize(transfer.done)}/${fmtSize(transfer.total)}",
                     style = MaterialTheme.typography.labelSmall)
                Spacer(Modifier.height(4.dp))
                LinearProgressIndicator(progress = { transfer.fraction },
                                        modifier = Modifier.fillMaxWidth().height(4.dp)
                                            .clip(RoundedCornerShape(2.dp)))
            }
        }

        val focus = LocalFocusManager.current
        val keyboard = LocalSoftwareKeyboardController.current
        LaunchedEffect(listState) {
            snapshotFlow { listState.isScrollInProgress }.collect { scrolling ->
                if (scrolling) { focus.clearFocus(); keyboard?.hide() }
            }
        }
        LazyColumn(state = listState,
                   modifier = Modifier.weight(1f).fillMaxWidth()
                       .pointerInput(Unit) {
                           detectTapGestures { focus.clearFocus(); keyboard?.hide() }
                       },
                   contentPadding = PaddingValues(12.dp)) {
            items(msgs) { m -> Bubble(m) }
        }

        DirectComposer(
            enabled = state == ConnState.Connected,
            onSend = { vm.sendDirect(it) },
            onAttach = { vm.pickImage() },
            onAttachVideo = { vm.pickMedia() },
        )
    }

    if (showFingerprint) {
        AlertDialog(
            onDismissRequest = { showFingerprint = false },
            confirmButton = {
                TextButton(onClick = { showFingerprint = false }) { Text("Done") }
            },
            icon = { Icon(Icons.Default.VerifiedUser, null) },
            title = { Text("Session fingerprint") },
            text = {
                Column {
                    Text(fp.ifBlank { "—" },
                         style = MaterialTheme.typography.titleLarge,
                         color = MaterialTheme.colorScheme.primary)
                    Spacer(Modifier.height(10.dp))
                    Text("Read this aloud to the other person. If it matches on " +
                         "both screens, nobody is in between.",
                         style = MaterialTheme.typography.bodyMedium)
                }
            },
        )
    }
}

/** Chat bubbles, mine on the right, theirs on the left. */
@Composable
private fun Bubble(m: Message) {
    val enter = remember {
        fadeIn(tween(200)) + slideInVertically(spring(Spring.DampingRatioLowBouncy)) { it / 3 }
    }
    AnimatedVisibility(visible = true, enter = enter) {
        Row(Modifier.fillMaxWidth().padding(vertical = 3.dp),
            horizontalArrangement = if (m.mine) Arrangement.End else Arrangement.Start) {
            Surface(
                color = if (m.mine) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.surfaceVariant,
                shape = RoundedCornerShape(
                    topStart = 16.dp, topEnd = 16.dp,
                    bottomStart = if (m.mine) 16.dp else 4.dp,
                    bottomEnd = if (m.mine) 4.dp else 16.dp),
                modifier = Modifier.widthIn(max = 300.dp),
            ) {
                Column(Modifier.padding(10.dp)) {
                    m.inlineImage?.let { bytes ->
                        val bmp = remember(bytes) {
                            runCatching {
                                android.graphics.BitmapFactory
                                    .decodeByteArray(bytes, 0, bytes.size)
                            }.getOrNull()
                        }
                        bmp?.let {
                            androidx.compose.foundation.Image(
                                bitmap = it.asImageBitmap(), contentDescription = null,
                                modifier = Modifier.heightIn(max = 240.dp)
                                    .clip(RoundedCornerShape(10.dp)))
                            Spacer(Modifier.height(6.dp))
                        }
                    }
                    m.media?.takeIf { it.kind != "img" }?.let {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(if (it.kind == "aud") Icons.Default.MusicNote
                                 else Icons.Default.Movie, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(8.dp))
                            Text("${it.display} · ${fmtSize(it.size)}",
                                 style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                    m.body?.let {
                        Text(it, style = MaterialTheme.typography.bodyLarge,
                             color = if (m.mine) MaterialTheme.colorScheme.onPrimary
                                     else MaterialTheme.colorScheme.onSurface)
                    }
                    Text(clock.format(Date(m.timestamp)),
                         style = MaterialTheme.typography.labelSmall,
                         color = (if (m.mine) MaterialTheme.colorScheme.onPrimary
                                  else MaterialTheme.colorScheme.onSurfaceVariant)
                             .copy(alpha = 0.65f),
                         modifier = Modifier.align(Alignment.End))
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DirectComposer(enabled: Boolean, onSend: (String) -> Unit,
                           onAttach: () -> Unit, onAttachVideo: () -> Unit) {
    var text by remember { mutableStateOf("") }
    var showAttach by remember { mutableStateOf(false) }
    Surface(tonalElevation = 3.dp) {
        Column(Modifier.windowInsetsPadding(
                   WindowInsets.ime.union(WindowInsets.navigationBars))) {
            AnimatedVisibility(showAttach, enter = expandVertically() + fadeIn(),
                               exit = shrinkVertically() + fadeOut()) {
                Row(Modifier.fillMaxWidth().padding(12.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    AttachChip("Picture", Icons.Default.Image) {
                        showAttach = false; onAttach()
                    }
                    AttachChip("Video", Icons.Default.Movie) {
                        showAttach = false; onAttachVideo()
                    }
                }
            }
            Row(Modifier.fillMaxWidth().padding(8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { showAttach = !showAttach }, enabled = enabled) {
                    Icon(if (showAttach) Icons.Default.Close else Icons.Default.Add,
                         "attach")
                }
                TextField(
                    value = text, onValueChange = { text = it },
                    modifier = Modifier.weight(1f), enabled = enabled, maxLines = 5,
                    placeholder = { Text(if (enabled) "Message" else "Not connected") },
                    shape = RoundedCornerShape(22.dp),
                    colors = TextFieldDefaults.colors(
                        focusedIndicatorColor = Color.Transparent,
                        unfocusedIndicatorColor = Color.Transparent,
                        disabledIndicatorColor = Color.Transparent),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = {
                        if (text.isNotBlank()) { onSend(text.trim()); text = "" }
                    }),
                )
                Spacer(Modifier.width(6.dp))
                FilledIconButton(
                    onClick = { if (text.isNotBlank()) { onSend(text.trim()); text = "" } },
                    enabled = enabled && text.isNotBlank(),
                ) { Icon(Icons.AutoMirrored.Filled.Send, "send") }
            }
        }
    }
}

@Composable
private fun AttachChip(label: String, icon: androidx.compose.ui.graphics.vector.ImageVector,
                       onClick: () -> Unit) {
    AssistChip(onClick = onClick, label = { Text(label) },
               leadingIcon = { Icon(icon, null, Modifier.size(18.dp)) })
}

/** Someone is asking to chat: show who, and the fingerprint, before deciding. */
@Composable
fun IncomingRequestDialog(chat: DirectChat, onAccept: () -> Unit, onReject: () -> Unit) {
    val peer by chat.peerNick.collectAsState()
    val fp by chat.fingerprint.collectAsState()
    AlertDialog(
        onDismissRequest = onReject,
        icon = { Icon(Icons.Default.PersonAdd, null) },
        title = { Text("“$peer” wants to chat") },
        text = {
            Column {
                Text("Nobody can send you anything until you accept.",
                     style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(12.dp))
                Text("Session fingerprint", style = MaterialTheme.typography.labelSmall,
                     color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(fp, style = MaterialTheme.typography.titleMedium,
                     color = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.height(6.dp))
                Text("Check it matches on their screen.",
                     style = MaterialTheme.typography.labelSmall,
                     color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        },
        confirmButton = { Button(onClick = onAccept) { Text("Accept") } },
        dismissButton = { TextButton(onClick = onReject) { Text("Decline") } },
    )
}

/** Start a chat, and share this phone's own address. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StartChatSheet(vm: AppViewModel, onClose: () -> Unit) {
    val myAddress by vm.myAddress.collectAsState()
    val contacts by vm.contacts.collectAsState()
    var address by remember { mutableStateOf("") }
    var label by remember { mutableStateOf("") }
    val clipboard = LocalClipboardManager.current

    val pairing by vm.pairingCode.collectAsState()
    var theirCode by remember { mutableStateOf("") }

    ModalBottomSheet(onDismissRequest = onClose) {
        Column(Modifier.padding(22.dp)
                   .verticalScroll(androidx.compose.foundation.rememberScrollState())
                   .navigationBarsPadding()) {
            Text("Direct chat", style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.height(4.dp))
            Text("Just the two of you — no server involved, and nobody else can " +
                 "read it.", style = MaterialTheme.typography.bodyMedium,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)

            Spacer(Modifier.height(18.dp))
            Text("Pair with a code", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(4.dp))
            Text("Five digits, easier than reading out an address.",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(8.dp))

            AnimatedVisibility(pairing != null, enter = fadeIn() + expandVertically(),
                               exit = fadeOut() + shrinkVertically()) {
                Surface(color = MaterialTheme.colorScheme.primaryContainer,
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp),
                           horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(pairing.orEmpty(),
                             style = MaterialTheme.typography.titleLarge,
                             color = MaterialTheme.colorScheme.onPrimaryContainer)
                        Text("Read this to them — they type it to reach you",
                             style = MaterialTheme.typography.labelSmall,
                             color = MaterialTheme.colorScheme.onPrimaryContainer)
                    }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (pairing == null) {
                    Button(onClick = { vm.createChatCode() }) { Text("Get a code") }
                } else {
                    OutlinedButton(onClick = { vm.clearChatCode() }) { Text("Stop") }
                }
            }
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = theirCode,
                    onValueChange = { theirCode = it.filter(Char::isDigit).take(5) },
                    label = { Text("Their code") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Number),
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                Button(onClick = { vm.chatWithCode(theirCode); onClose() },
                       enabled = theirCode.length == 5) { Text("Connect") }
            }

            HorizontalDivider(Modifier.padding(vertical = 16.dp))
            Text("Your address", style = MaterialTheme.typography.titleMedium)
            val addressError by vm.addressError.collectAsState()
            Surface(color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.fillMaxWidth().padding(top = 6.dp)) {
                Row(Modifier.padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(myAddress ?: addressError ?: "publishing…",
                         Modifier.weight(1f),
                         style = MaterialTheme.typography.bodyMedium, maxLines = 3,
                         color = if (myAddress == null && addressError != null)
                             MaterialTheme.colorScheme.error
                         else MaterialTheme.colorScheme.onSurfaceVariant)
                    if (myAddress != null) {
                        IconButton(onClick = {
                            clipboard.setText(AnnotatedString(myAddress!!))
                        }) { Icon(Icons.Default.ContentCopy, "copy") }
                    } else if (addressError != null) {
                        TextButton(onClick = { vm.retryAddress() }) { Text("Retry") }
                    }
                }
            }
            Text("Give this to someone so they can start a chat with you.",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)

            HorizontalDivider(Modifier.padding(vertical = 16.dp))
            Text("Start one", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(8.dp))
            if (contacts.isNotEmpty()) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    contacts.take(4).forEach { c ->
                        AssistChip(onClick = { vm.startChat(c.onion, c.name); onClose() },
                                   label = { Text(c.name) })
                    }
                }
                Spacer(Modifier.height(10.dp))
            }
            OutlinedTextField(address, { address = it },
                label = { Text("Their onion address") }, singleLine = true,
                modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(label, { label = it },
                label = { Text("Save as (optional)") }, singleLine = true,
                modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(16.dp))
            Button(onClick = { vm.startChat(address, label); onClose() },
                   enabled = address.isNotBlank(),
                   modifier = Modifier.fillMaxWidth()) { Text("Start chat") }
            Spacer(Modifier.height(16.dp))
        }
    }
}

/** Direct chats listed alongside servers in the drawer. */
@Composable
fun DirectChatRow(chat: DirectChat, active: Boolean, onClick: () -> Unit) {
    val peer by chat.peerNick.collectAsState()
    val unread by chat.unread.collectAsState()
    val state by chat.state.collectAsState()
    val bg by animateColorAsState(
        if (active) MaterialTheme.colorScheme.primaryContainer else Color.Transparent,
        tween(200), label = "dm")
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 3.dp)
            .clip(RoundedCornerShape(12.dp)).background(bg)
            .clickable(onClick = onClick).padding(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(34.dp).clip(CircleShape)
                .background(MaterialTheme.colorScheme.secondary.copy(alpha = 0.2f)),
            contentAlignment = Alignment.Center) {
            Text(peer.take(1).uppercase(), fontWeight = FontWeight.Bold,
                 color = MaterialTheme.colorScheme.secondary)
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(peer, style = MaterialTheme.typography.titleMedium,
                 maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(if (state == ConnState.Connected) "connected" else "offline",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        AnimatedVisibility(unread > 0, enter = scaleIn() + fadeIn(),
                           exit = scaleOut() + fadeOut()) {
            Box(Modifier.clip(CircleShape)
                    .background(MaterialTheme.colorScheme.primary)
                    .padding(horizontal = 6.dp, vertical = 1.dp)) {
                Text("$unread", color = MaterialTheme.colorScheme.onPrimary,
                     style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}
