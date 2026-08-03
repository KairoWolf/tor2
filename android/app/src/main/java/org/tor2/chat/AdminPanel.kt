package org.tor2.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp

/**
 * Running the server from the phone: channels, members and invitations,
 * so an admin does not have to reach for a laptop.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AdminSheet(vm: AppViewModel, client: ServerClient, onClose: () -> Unit) {
    val channels by client.channels.collectAsState()
    val online by client.online.collectAsState()
    val notice by client.notice.collectAsState()
    val clipboard = LocalClipboardManager.current

    var newChannel by remember { mutableStateOf("") }
    var who by remember { mutableStateOf("") }
    var reason by remember { mutableStateOf("") }
    var codeUses by remember { mutableStateOf("1") }
    var codeAdmin by remember { mutableStateOf(false) }

    ModalBottomSheet(onDismissRequest = onClose) {
        Column(Modifier.padding(22.dp).verticalScroll(rememberScrollState())
                   .navigationBarsPadding()) {
            Text("Manage server", style = MaterialTheme.typography.titleLarge)
            Text("You are an admin here.",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)

            // whatever the server last told us, including minted codes
            AnimatedVisibility(notice != null, enter = expandVertically() + fadeIn(),
                               exit = shrinkVertically() + fadeOut()) {
                Surface(color = MaterialTheme.colorScheme.primaryContainer,
                        shape = RoundedCornerShape(10.dp),
                        modifier = Modifier.fillMaxWidth().padding(top = 12.dp)) {
                    Row(Modifier.padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Text(notice.orEmpty(), Modifier.weight(1f),
                             style = MaterialTheme.typography.bodyMedium,
                             color = MaterialTheme.colorScheme.onPrimaryContainer)
                        IconButton(onClick = {
                            clipboard.setText(AnnotatedString(notice.orEmpty()))
                        }) { Icon(Icons.Default.ContentCopy, "copy") }
                    }
                }
            }

            SectionTitle("Invite someone")
            Text("An 8-digit code is the whole invitation — no address needed.",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = codeUses,
                    onValueChange = { codeUses = it.filter(Char::isDigit).take(3) },
                    label = { Text("Uses") },
                    singleLine = true,
                    modifier = Modifier.width(110.dp),
                )
                Spacer(Modifier.width(10.dp))
                FilterChip(selected = codeAdmin, onClick = { codeAdmin = !codeAdmin },
                           label = { Text("Admin") })
                Spacer(Modifier.width(10.dp))
                Button(onClick = {
                    val uses = codeUses.ifBlank { "1" }
                    vm.admin("joincode", if (codeAdmin) "$uses admin" else uses)
                }) { Text("Create code") }
            }

            SectionTitle("Channels")
            channels.forEach { ch ->
                Row(Modifier.fillMaxWidth().padding(vertical = 3.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Text("#${ch.name}", Modifier.weight(1f),
                         style = MaterialTheme.typography.bodyLarge)
                    if (channels.size > 1) {
                        TextButton(onClick = { vm.admin("rmchan", ch.name) }) {
                            Text("Delete", color = MaterialTheme.colorScheme.error)
                        }
                    }
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = newChannel,
                    onValueChange = {
                        newChannel = it.lowercase().filter { c ->
                            c.isLetterOrDigit() || c == '-' || c == '_'
                        }.take(24)
                    },
                    label = { Text("New channel") },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(10.dp))
                Button(onClick = { vm.admin("mkchan", newChannel); newChannel = "" },
                       enabled = newChannel.isNotBlank()) { Text("Add") }
            }

            SectionTitle("Members")
            if (online.isEmpty()) {
                Text("Nobody else is online.",
                     style = MaterialTheme.typography.bodyMedium,
                     color = MaterialTheme.colorScheme.onSurfaceVariant)
            } else {
                Row(Modifier.fillMaxWidth(), horizontalArrangement =
                        Arrangement.spacedBy(8.dp)) {
                    online.take(6).forEach { name ->
                        AssistChip(onClick = { who = name }, label = { Text(name) })
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = who, onValueChange = { who = it },
                label = { Text("Member") }, singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(6.dp))
            OutlinedTextField(
                value = reason, onValueChange = { reason = it },
                label = { Text("Reason (for a ban)") }, singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { vm.admin("kick", who) },
                               enabled = who.isNotBlank()) { Text("Kick") }
                OutlinedButton(
                    onClick = { vm.admin("ban", who, reason) },
                    enabled = who.isNotBlank(),
                    colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = MaterialTheme.colorScheme.error),
                ) { Text("Ban") }
                OutlinedButton(onClick = { vm.admin("unban", who) },
                               enabled = who.isNotBlank()) { Text("Unban") }
            }
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { vm.admin("promote", who) },
                               enabled = who.isNotBlank()) { Text("Make admin") }
                OutlinedButton(onClick = { vm.admin("demote", who) },
                               enabled = who.isNotBlank()) { Text("Remove admin") }
                TextButton(onClick = { vm.admin("bans") }) { Text("Who is banned") }
            }

            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Spacer(Modifier.height(18.dp))
    Text(text, style = MaterialTheme.typography.titleMedium)
    Spacer(Modifier.height(6.dp))
}
