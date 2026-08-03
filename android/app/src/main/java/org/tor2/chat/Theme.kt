package org.tor2.chat

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * A calm, dark-first palette: deep indigo surfaces with a warm violet accent,
 * so the app looks like its own thing rather than a default template.
 */
private val Onion = Color(0xFF8B7BF7)
private val OnionBright = Color(0xFFA99BFF)
private val Deep = Color(0xFF0E0D15)
private val Panel = Color(0xFF16151F)
private val Raised = Color(0xFF1E1D2A)
private val Mint = Color(0xFF5BD6A8)
private val Rose = Color(0xFFFF7A93)
private val Sand = Color(0xFFF4C77B)

private val DarkScheme = darkColorScheme(
    primary = Onion,
    onPrimary = Color(0xFF14121F),
    primaryContainer = Color(0xFF322B57),
    onPrimaryContainer = OnionBright,
    secondary = Mint,
    onSecondary = Color(0xFF06231A),
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
    MaterialTheme(
        colorScheme = if (dark) DarkScheme else LightScheme,
        typography = AppTypography,
        content = content,
    )
}
