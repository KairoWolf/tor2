package org.tor2.chat

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.net.Uri
import android.os.Bundle
import android.os.IBinder
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import org.torproject.jni.TorService

class MainActivity : AppCompatActivity() {

    private val vm: AppViewModel by viewModels()

    private val pickImage = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? -> uri?.let { sendPicked(it, "img") } }

    private val pickMedia = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? -> uri?.let { sendPicked(it, "vid") } }

    private val torConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            val binder = service as? TorService.LocalBinder ?: return
            // Reading the control connection and the port both block, and this
            // callback runs on the main thread — doing it here froze the app.
            lifecycleScope.launch(Dispatchers.IO) {
                val torService = binder.service
                // Ask again later too: it is null until Tor is actually up.
                vm.tor.controlProvider = {
                    runCatching { torService.torControlConnection }.getOrNull()
                }
                runCatching { torService.torControlConnection }
                    .getOrNull()?.let { vm.tor.attachControl(it) }
                val port = runCatching { torService.socksPort }.getOrDefault(0)
                    .takeIf { it > 0 } ?: TorService.socksPort
                if (port > 0) vm.tor.socksPort = port
                vm.onTorServiceReady()
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {}
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        vm.attachPickers(::launchImagePicker, ::launchMediaPicker)

        // Bind rather than startForegroundService: Android requires a
        // foreground service to call startForeground() within a few seconds,
        // and TorService does not always manage it — the system then kills the
        // app with "did not then call Service.startForeground()". A bound
        // service runs for as long as we are bound to it, with no such
        // contract.
        val intent = Intent(this, TorService::class.java)
        bindService(intent, torConnection, Context.BIND_AUTO_CREATE)

        setContent {
            Tor2Theme { MainScreen(vm) }
        }
    }

    private fun launchImagePicker() = pickImage.launch("image/*")
    private fun launchMediaPicker() = pickMedia.launch("video/*")

    private fun sendPicked(uri: Uri, kind: String) {
        val name = queryName(uri)
        val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: return
        if (kind == "img") vm.sendImage(bytes, name) else vm.sendFile(bytes, name, kind)
    }

    private fun queryName(uri: Uri): String {
        contentResolver.query(uri, null, null, null, null)?.use { c ->
            val idx = c.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
            if (idx >= 0 && c.moveToFirst()) return c.getString(idx) ?: "file"
        }
        return uri.lastPathSegment ?: "file"
    }

    override fun onDestroy() {
        runCatching { unbindService(torConnection) }
        super.onDestroy()
    }
}
