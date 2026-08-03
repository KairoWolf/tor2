package org.tor2.chat

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

/** The whole app: Tor warm-up, then the server drawer and chat. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(vm: AppViewModel) {
    val ready by vm.torReady.collectAsState()
    val client by vm.current.collectAsState()
    val banner by vm.banner.collectAsState()
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    var showJoin by remember { mutableStateOf(false) }
    var showSettings by remember { mutableStateOf(false) }
    var showMembers by remember { mutableStateOf(false) }
    var showStartChat by remember { mutableStateOf(false) }
    var infoFor by remember { mutableStateOf<SavedServer?>(null) }
    var showAdmin by remember { mutableStateOf(false) }
    val openChat by vm.openChat.collectAsState()
    val incoming by vm.incomingRequest.collectAsState()

    Crossfade(targetState = ready, animationSpec = tween(500), label = "boot") { isReady ->
        if (!isReady) {
            BootScreen(vm, banner)
        } else {
            ModalNavigationDrawer(
                drawerState = drawer,
                drawerContent = {
                    ServerDrawer(
                        vm = vm,
                        onJoin = { showJoin = true },
                        onStartChat = { showStartChat = true },
                        onSettings = { showSettings = true },
                        onInfo = { infoFor = it },
                        onPicked = { scope.launch { drawer.close() } },
                    )
                },
            ) {
                val snackbar = remember { SnackbarHostState() }
                LaunchedEffect(banner) {
                    banner?.let {
                        snackbar.showSnackbar(it)
                        vm.banner.value = null
                    }
                }
                Scaffold(
                    snackbarHost = { SnackbarHost(snackbar) },
                    // the composer handles the keyboard inset itself, so the
                    // scaffold must not consume it first
                    contentWindowInsets = WindowInsets.statusBars,
                ) { padding ->
                    Box(Modifier.padding(padding)) {
                        val c = client
                        val dm = openChat
                        AnimatedContent(
                            targetState = when {
                                dm != null -> "dm"
                                c != null -> "server"
                                else -> "empty"
                            },
                            transitionSpec = {
                                (fadeIn(tween(220)) + slideInHorizontally { it / 6 })
                                    .togetherWith(fadeOut(tween(160)))
                            },
                            label = "pane",
                        ) { which ->
                            when (which) {
                                "dm" -> dm?.let {
                                    DirectPane(vm, it) { vm.openChat.value = null }
                                }
                                "server" -> c?.let {
                                    ChatPane(
                                        vm = vm, client = it,
                                        onOpenChannels = { scope.launch { drawer.open() } },
                                        onOpenMembers = { showMembers = true },
                                        onOpenAdmin = { showAdmin = true },
                                    )
                                }
                                else -> NoServerYet(
                                    onJoin = { showJoin = true },
                                    onStartChat = { showStartChat = true },
                                    onOpenDrawer = { scope.launch { drawer.open() } })
                            }
                        }
                    }
                }
            }
        }
    }

    if (showJoin) JoinSheet(vm) { showJoin = false }
    if (showStartChat) StartChatSheet(vm) { showStartChat = false }
    infoFor?.let { saved ->
        ServerInfoSheet(vm, saved,
                        client.takeIf { it?.saved?.key == saved.key }) {
            infoFor = null
        }
    }
    incoming?.let {
        IncomingRequestDialog(it, onAccept = { vm.acceptIncoming() },
                              onReject = { vm.rejectIncoming() })
    }
    if (showSettings) SettingsSheet(vm) { showSettings = false }
    if (showMembers) client?.let { MembersSheet(it) { showMembers = false } }
    if (showAdmin) client?.let { AdminSheet(vm, it) { showAdmin = false } }
    PreviewOverlay(client)
    val playing by vm.playing.collectAsState()
    PlayerOverlay(playing?.first, playing?.second ?: "") { vm.closePlayer() }
}

/** Tor takes a little while; make the wait feel deliberate rather than broken. */
@Composable
private fun BootScreen(vm: AppViewModel, banner: String?) {
    val percent by vm.bootstrap.collectAsState()
    val pulse = rememberInfiniteTransition(label = "pulse")
    val scale by pulse.animateFloat(
        initialValue = 0.94f, targetValue = 1.06f,
        animationSpec = infiniteRepeatable(tween(1400, easing = FastOutSlowInEasing),
                                           RepeatMode.Reverse), label = "scale")
    val spin by pulse.animateFloat(
        0f, 360f, infiniteRepeatable(tween(9000, easing = LinearEasing)), label = "spin")

    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background),
        contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Box(contentAlignment = Alignment.Center) {
                Box(
                    Modifier.size((132 * scale).dp).rotate(spin)
                        .border(1.dp,
                                MaterialTheme.colorScheme.primary.copy(alpha = 0.25f),
                                RoundedCornerShape(38.dp))
                )
                Box(
                    Modifier.size((96 * scale).dp).clip(CircleShape).background(
                        MaterialTheme.colorScheme.primary.copy(alpha = 0.14f)),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(Icons.Default.Lock, null, Modifier.size(40.dp),
                         tint = MaterialTheme.colorScheme.primary)
                }
            }
            Spacer(Modifier.height(28.dp))
            Text("tor2", style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.height(6.dp))
            Text(banner ?: "Connecting to the Tor network…",
                 style = MaterialTheme.typography.bodyMedium,
                 color = if (banner != null) MaterialTheme.colorScheme.error
                         else MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(20.dp))
            LinearProgressIndicator(
                progress = { percent / 100f },
                modifier = Modifier.width(190.dp).height(4.dp)
                    .clip(RoundedCornerShape(2.dp)),
            )
            Spacer(Modifier.height(8.dp))
            Text("$percent%", style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun NoServerYet(onJoin: () -> Unit, onStartChat: () -> Unit,
                        onOpenDrawer: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(30.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(Icons.Default.Forum, null, Modifier.size(58.dp),
             tint = MaterialTheme.colorScheme.primary.copy(alpha = 0.75f))
        Spacer(Modifier.height(18.dp))
        Text("No servers yet", style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(8.dp))
        Text("Ask an admin for an invite code, then join with the server's " +
             "onion address.",
             style = MaterialTheme.typography.bodyMedium,
             color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(22.dp))
        Button(onClick = onJoin) {
            Icon(Icons.Default.Add, null, Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text("Join a server")
        }
        Spacer(Modifier.height(6.dp))
        OutlinedButton(onClick = onStartChat) {
            Icon(Icons.Default.Chat, null, Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text("Start a direct chat")
        }
        TextButton(onClick = onOpenDrawer) { Text("Open menu") }
    }
}

/** Servers down the side, channels underneath the active one. */
@Composable
private fun ServerDrawer(vm: AppViewModel, onJoin: () -> Unit,
                         onStartChat: () -> Unit, onSettings: () -> Unit,
                         onInfo: (SavedServer) -> Unit, onPicked: () -> Unit) {
    val servers by vm.savedServers.collectAsState()
    val client by vm.current.collectAsState()
    val nick by vm.nick.collectAsState()
    val chats by vm.chats.collectAsState()
    val openChat by vm.openChat.collectAsState()

    ModalDrawerSheet(Modifier.width(300.dp)) {
        Column(Modifier.fillMaxSize()) {
            Row(Modifier.fillMaxWidth().padding(18.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.Lock, null,
                     tint = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text("tor2", style = MaterialTheme.typography.titleLarge)
                    Text(nick, style = MaterialTheme.typography.labelSmall,
                         color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                IconButton(onClick = onSettings) {
                    Icon(Icons.Default.Settings, "settings")
                }
            }
            HorizontalDivider()

            LazyColumn(Modifier.weight(1f)) {
                item { DrawerLabel("Servers") }
                items(servers, key = { it.key }) { s ->
                    val isActive = client?.saved?.key == s.key
                    ServerRow(s, isActive, onClick = { vm.open(s); onPicked() },
                              onLongClick = { onInfo(s) })
                    AnimatedVisibility(
                        visible = isActive,
                        enter = expandVertically(spring(Spring.DampingRatioMediumBouncy)) + fadeIn(),
                        exit = shrinkVertically() + fadeOut(),
                    ) {
                        client?.let { ChannelList(it) { name ->
                            vm.switchChannel(name); onPicked()
                        } }
                    }
                }
                item {
                    TextButton(onClick = onJoin,
                               modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp)) {
                        Icon(Icons.Default.Add, null, Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("Join a server")
                    }
                }
                item { DrawerLabel("Direct chats") }
                items(chats, key = { it.key }) { chat ->
                    DirectChatRow(chat, openChat === chat) {
                        vm.openDirect(chat); onPicked()
                    }
                }
                item {
                    TextButton(onClick = onStartChat,
                               modifier = Modifier.fillMaxWidth().padding(12.dp)) {
                        Icon(Icons.Default.Chat, null, Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("Start a direct chat")
                    }
                }
            }
        }
    }
}

@Composable
private fun DrawerLabel(text: String) {
    Text(text.uppercase(), Modifier.padding(start = 18.dp, top = 10.dp, bottom = 4.dp),
         style = MaterialTheme.typography.labelSmall,
         color = MaterialTheme.colorScheme.onSurfaceVariant)
}

@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
private fun ServerRow(s: SavedServer, active: Boolean, onClick: () -> Unit,
                      onLongClick: () -> Unit) {
    val bg by animateColorAsState(
        if (active) MaterialTheme.colorScheme.primaryContainer else Color.Transparent,
        tween(220), label = "srv")
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 3.dp)
            .clip(RoundedCornerShape(12.dp)).background(bg)
            .combinedClickable(onClick = onClick, onLongClick = onLongClick)
            .padding(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(36.dp).clip(RoundedCornerShape(if (active) 12.dp else 18.dp))
                .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.25f)),
            contentAlignment = Alignment.Center,
        ) {
            Text(s.displayName.take(2).uppercase(),
                 fontWeight = FontWeight.Bold, fontSize = 13.sp,
                 color = MaterialTheme.colorScheme.primary)
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(s.displayName, style = MaterialTheme.typography.titleMedium,
                 maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text("hold for address and settings",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        IconButton(onClick = onLongClick, modifier = Modifier.size(30.dp)) {
            Icon(Icons.Default.MoreVert, "server options",
                 tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun ChannelList(client: ServerClient, onPick: (String) -> Unit) {
    val channels by client.channels.collectAsState()
    val active by client.active.collectAsState()
    Column(Modifier.padding(start = 22.dp, end = 10.dp, bottom = 6.dp)) {
        channels.forEach { ch ->
            val selected = ch.name == active
            val bg by animateColorAsState(
                if (selected) MaterialTheme.colorScheme.surfaceVariant else Color.Transparent,
                tween(180), label = "chan")
            Row(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(9.dp)).background(bg)
                    .clickable { onPick(ch.name) }
                    .padding(horizontal = 10.dp, vertical = 7.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("#", color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.width(6.dp))
                Text(ch.name, Modifier.weight(1f),
                     style = MaterialTheme.typography.bodyMedium,
                     fontWeight = if (selected || ch.unread > 0) FontWeight.SemiBold
                                  else FontWeight.Normal,
                     maxLines = 1, overflow = TextOverflow.Ellipsis)
                UnreadBadge(ch)
            }
        }
    }
}

@Composable
private fun UnreadBadge(ch: Channel) {
    AnimatedVisibility(ch.unread > 0 || ch.mentioned,
                       enter = scaleIn(spring(Spring.DampingRatioMediumBouncy)) + fadeIn(),
                       exit = scaleOut() + fadeOut()) {
        Box(
            Modifier.clip(CircleShape).background(
                if (ch.mentioned) MaterialTheme.colorScheme.error
                else MaterialTheme.colorScheme.primary)
                .padding(horizontal = 6.dp, vertical = 1.dp),
        ) {
            Text(if (ch.mentioned) "@" else "${ch.unread}",
                 color = MaterialTheme.colorScheme.onPrimary,
                 style = MaterialTheme.typography.labelSmall)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun JoinSheet(vm: AppViewModel, onClose: () -> Unit) {
    var code by remember { mutableStateOf("") }
    var address by remember { mutableStateOf("") }
    var invite by remember { mutableStateOf("") }
    var label by remember { mutableStateOf("") }
    var manual by remember { mutableStateOf(false) }

    ModalBottomSheet(onDismissRequest = onClose) {
        Column(Modifier.padding(22.dp).verticalScroll(rememberScrollState())
                   .navigationBarsPadding()) {
            Text("Join a server", style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.height(6.dp))
            Text("Ask an admin for a join code — eight digits is all you need.",
                 style = MaterialTheme.typography.bodyMedium,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(16.dp))

            OutlinedTextField(
                value = code,
                onValueChange = { code = it.filter(Char::isDigit).take(8) },
                label = { Text("Join code") },
                placeholder = { Text("48213902") },
                singleLine = true,
                textStyle = MaterialTheme.typography.titleLarge,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(14.dp))
            Button(
                onClick = { vm.joinWithCode(code, label); onClose() },
                enabled = code.length == 8,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Join with code") }

            TextButton(onClick = { manual = !manual }) {
                Text(if (manual) "Hide the manual way"
                     else "I only have an address and invite")
            }

            AnimatedVisibility(manual, enter = expandVertically() + fadeIn(),
                               exit = shrinkVertically() + fadeOut()) {
                Column {
                    OutlinedTextField(address, { address = it },
                        label = { Text("Onion address") },
                        placeholder = { Text("abcd…onion") },
                        singleLine = true, modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.height(10.dp))
                    OutlinedTextField(invite, { invite = it },
                        label = { Text("Invite code") },
                        placeholder = { Text("kfpr-2xmq-nwte") },
                        singleLine = true, modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.height(10.dp))
                    OutlinedTextField(label, { label = it },
                        label = { Text("Name it (optional)") },
                        singleLine = true, modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.height(14.dp))
                    OutlinedButton(
                        onClick = { vm.joinServer(address, invite, label); onClose() },
                        enabled = address.isNotBlank() && invite.isNotBlank(),
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text("Join with address") }
                }
            }
            Spacer(Modifier.height(16.dp))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsSheet(vm: AppViewModel, onClose: () -> Unit) {
    val nick by vm.nick.collectAsState()
    var name by remember { mutableStateOf(nick) }
    var sound by remember { mutableStateOf(vm.setting("notify_sound", false)) }
    var vibrate by remember { mutableStateOf(vm.setting("notify_vibrate", false)) }
    var everything by remember { mutableStateOf(vm.setting("notify_all", false)) }
    var previews by remember { mutableStateOf(vm.setting("show_previews", true)) }

    ModalBottomSheet(onDismissRequest = onClose) {
        Column(Modifier.padding(22.dp).verticalScroll(rememberScrollState())
                   .navigationBarsPadding()) {
            Text("Settings", style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(name, { name = it },
                label = { Text("Display name") }, singleLine = true,
                modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(6.dp))
            TextButton(onClick = { vm.setNick(name) }) { Text("Save name") }

            HorizontalDivider(Modifier.padding(vertical = 12.dp))
            Text("Notifications", style = MaterialTheme.typography.titleMedium)
            Text("Off unless you turn them on.",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(6.dp))
            SettingSwitch("Sound", "Chime when you are mentioned", sound) {
                sound = it; vm.setSetting("notify_sound", it)
            }
            SettingSwitch("Vibrate", "Buzz when you are mentioned", vibrate) {
                vibrate = it; vm.setSetting("notify_vibrate", it)
            }
            SettingSwitch("Alert on every message",
                          "Not just mentions", everything) {
                everything = it; vm.setSetting("notify_all", it)
            }

            HorizontalDivider(Modifier.padding(vertical = 12.dp))
            Text("Appearance", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(6.dp))
            SettingSwitch("Show pictures in the chat",
                          "Turn off to save data", previews) {
                previews = it; vm.setSetting("show_previews", it)
            }

            HorizontalDivider(Modifier.padding(vertical = 12.dp))
            Text("Lawful use only. Messages in a server can be read by whoever " +
                 "runs it; direct chats stay end-to-end encrypted.",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(20.dp))
        }
    }
}

@Composable
private fun SettingSwitch(title: String, help: String, value: Boolean,
                          onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(help, style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked = value, onCheckedChange = onChange)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MembersSheet(client: ServerClient, onClose: () -> Unit) {
    val online by client.online.collectAsState()
    val fp by client.fingerprint.collectAsState()
    ModalBottomSheet(onDismissRequest = onClose) {
        Column(Modifier.padding(22.dp).navigationBarsPadding()) {
            Text("Online now", style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.height(12.dp))
            if (online.isEmpty()) {
                Text("Nobody else is here right now.",
                     color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            online.forEach {
                Row(Modifier.padding(vertical = 7.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(8.dp).clip(CircleShape)
                            .background(MaterialTheme.colorScheme.secondary))
                    Spacer(Modifier.width(10.dp))
                    Text(it, style = MaterialTheme.typography.bodyLarge)
                }
            }
            HorizontalDivider(Modifier.padding(vertical = 14.dp))
            Text("Session fingerprint", style = MaterialTheme.typography.titleMedium)
            Text(fp.ifBlank { "—" }, style = MaterialTheme.typography.bodyLarge,
                 color = MaterialTheme.colorScheme.primary)
            Text("Compare this with the other end to be sure nobody is in between.",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(20.dp))
        }
    }
}

/** A full-width still, fetched by the preview button. */
@Composable
private fun PreviewOverlay(client: ServerClient?) {
    val bytes by (client?.previewReady ?: remember { kotlinx.coroutines.flow.MutableStateFlow(null) })
        .collectAsState()
    AnimatedVisibility(bytes != null, enter = fadeIn(tween(200)),
                       exit = fadeOut(tween(150))) {
        val data = bytes ?: return@AnimatedVisibility
        val bitmap = remember(data) {
            runCatching {
                android.graphics.BitmapFactory.decodeByteArray(data, 0, data.size)
            }.getOrNull()
        }
        Box(
            Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.85f))
                .clickable { client?.previewReady?.value = null },
            contentAlignment = Alignment.Center,
        ) {
            bitmap?.let {
                androidx.compose.foundation.Image(
                    bitmap = it.asImageBitmap(),
                    contentDescription = "preview",
                    modifier = Modifier.fillMaxWidth().padding(18.dp)
                        .clip(RoundedCornerShape(14.dp)),
                )
            }
        }
    }
}
