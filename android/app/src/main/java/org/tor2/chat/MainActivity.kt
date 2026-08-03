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
import androidx.core.content.ContextCompat
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
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {}
        override fun onServiceDisconnected(name: ComponentName?) {}
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        vm.attachPickers(::launchImagePicker, ::launchMediaPicker)

        // Tor runs in its own service so it survives the screen turning off
        val intent = Intent(this, TorService::class.java)
        ContextCompat.startForegroundService(this, intent)
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
