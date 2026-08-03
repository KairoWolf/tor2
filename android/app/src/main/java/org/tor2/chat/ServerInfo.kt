package org.tor2.chat

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Logout
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

/**
 * Everything about one server in a single place: what it is called, its
 * address (so you can pass it on without hunting for it), who is online, and
 * the way out.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ServerInfoSheet(
    vm: AppViewModel,
    saved: SavedServer,
    client: ServerClient?,
    onClose: () -> Unit,
) {
    val clipboard = LocalClipboardManager.current
    val context = LocalContext.current
    val online by (client?.online ?: remember {
        kotlinx.coroutines.flow.MutableStateFlow(emptyList<String>())
    }).collectAsState()
    val channels by (client?.channels ?: remember {
        kotlinx.coroutines.flow.MutableStateFlow(emptyList<Channel>())
    }).collectAsState()
    val isAdmin by (client?.isAdmin ?: remember {
        kotlinx.coroutines.flow.MutableStateFlow(false)
    }).collectAsState()
    var confirmLeave by remember { mutableStateOf(false) }

    ModalBottomSheet(onDismissRequest = onClose) {
        Column(Modifier.padding(22.dp).verticalScroll(rememberScrollState())
                   .navigationBarsPadding()) {
            Text(saved.displayName, style = MaterialTheme.typography.titleLarge)
            Text(
                buildString {
                    append(if (client != null) "connected" else "not connected")
                    if (isAdmin) append(" · admin")
                    if (channels.isNotEmpty()) append(" · ${channels.size} channels")
                },
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Spacer(Modifier.height(18.dp))
            Text("Server address", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(6.dp))
            Surface(color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier.fillMaxWidth()) {
                Text(saved.onion, Modifier.padding(12.dp),
                     style = MaterialTheme.typography.bodyMedium)
            }
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AssistChip(
                    onClick = { clipboard.setText(AnnotatedString(saved.onion)) },
                    label = { Text("Copy") },
                    leadingIcon = {
                        Icon(Icons.Default.ContentCopy, null, Modifier.size(16.dp))
                    })
                AssistChip(
                    onClick = { shareText(context, saved.onion) },
                    label = { Text("Share") },
                    leadingIcon = {
                        Icon(Icons.Default.Share, null, Modifier.size(16.dp))
                    })
            }

            if (online.isNotEmpty()) {
                Spacer(Modifier.height(18.dp))
                Text("Online now", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(4.dp))
                Text(online.joinToString(", "),
                     style = MaterialTheme.typography.bodyMedium,
                     color = MaterialTheme.colorScheme.onSurfaceVariant)
            }

            HorizontalDivider(Modifier.padding(vertical = 18.dp))

            OutlinedButton(
                onClick = { confirmLeave = true },
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.outlinedButtonColors(
                    contentColor = MaterialTheme.colorScheme.error),
            ) {
                Icon(Icons.Default.Logout, null, Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("Leave this server")
            }
            Text("Removes it from this phone. You would need a new invite to " +
                 "come back.",
                 style = MaterialTheme.typography.labelSmall,
                 color = MaterialTheme.colorScheme.onSurfaceVariant,
                 modifier = Modifier.padding(top = 6.dp))
            Spacer(Modifier.height(20.dp))
        }
    }

    if (confirmLeave) {
        AlertDialog(
            onDismissRequest = { confirmLeave = false },
            title = { Text("Leave ${saved.displayName}?") },
            text = {
                Text("It is removed from this phone, along with your saved " +
                     "membership. Rejoining needs a fresh invite code.")
            },
            confirmButton = {
                Button(
                    onClick = { confirmLeave = false; vm.leave(saved); onClose() },
                    colors = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.error),
                ) { Text("Leave") }
            },
            dismissButton = {
                TextButton(onClick = { confirmLeave = false }) { Text("Stay") }
            },
        )
    }
}

private fun shareText(context: android.content.Context, text: String) {
    val intent = android.content.Intent(android.content.Intent.ACTION_SEND).apply {
        type = "text/plain"
        putExtra(android.content.Intent.EXTRA_TEXT, text)
    }
    runCatching {
        context.startActivity(
            android.content.Intent.createChooser(intent, "Share address"))
    }
}
