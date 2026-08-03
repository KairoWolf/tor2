package org.tor2.chat

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * A calm, dark-first palette: deep indigo surfaces with a warm violet accent,
 * so the app looks like its own thing rather than a default template.
 */
// Deep enough that white sits on it comfortably — black text on a light
// violet read as odd on a phone.
private val Onion = Color(0xFF6C5CE0)
private val OnionBright = Color(0xFFB9AEFF)
private val Deep = Color(0xFF0E0D15)
private val Panel = Color(0xFF16151F)
private val Raised = Color(0xFF1E1D2A)
private val Mint = Color(0xFF5BD6A8)
private val Rose = Color(0xFFFF7A93)
private val Sand = Color(0xFFF4C77B)

private val DarkScheme = darkColorScheme(
    primary = Onion,
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFF2C2551),
    onPrimaryContainer = OnionBright,
    secondary = Mint,
    onSecondary = Color(0xFF05231A),
    secondaryContainer = Color(0xFF14352B),
    onSecondaryContainer = Mint,
    tertiary = Sand,
    background = Deep,
    onBackground = Color(0xFFE9E7F2),
    surface = Panel,
    onSurface = Color(0xFFE9E7F2),
    surfaceVariant = Raised,
    onSurfaceVariant = Color(0xFFAFACC4),
    error = Rose,
    outline = Color(0xFF3A3750),
)

private val LightScheme = lightColorScheme(
    primary = Color(0xFF5B4BD6),
    onPrimary = Color.White,
    onSecondary = Color.White,
    primaryContainer = Color(0xFFE5E0FF),
    onPrimaryContainer = Color(0xFF1B1240),
    secondary = Color(0xFF1E9E74),
    background = Color(0xFFFBFAFF),
    surface = Color.White,
    surfaceVariant = Color(0xFFF0EEF8),
    onSurfaceVariant = Color(0xFF565270),
    error = Color(0xFFC4304C),
    outline = Color(0xFFD6D2E6),
)

private val AppTypography = Typography(
    titleLarge = TextStyle(fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold, fontSize = 21.sp),
    titleMedium = TextStyle(fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold, fontSize = 16.sp),
    bodyLarge = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 15.sp,
        lineHeight = 21.sp),
    bodyMedium = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 14.sp),
    labelSmall = TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 11.sp,
        fontWeight = FontWeight.Medium),
)

@Composable
fun Tor2Theme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val scheme = if (dark) DarkScheme else LightScheme
    MaterialTheme(colorScheme = scheme, typography = AppTypography) {
        // Wrapping in a Surface sets the default content colour. Without it
        // Compose falls back to black, which is invisible on a dark theme —
        // the app title was unreadable on the first build.
        Surface(color = scheme.background, contentColor = scheme.onBackground,
                modifier = Modifier.fillMaxSize()) {
            content()
        }
    }
}
