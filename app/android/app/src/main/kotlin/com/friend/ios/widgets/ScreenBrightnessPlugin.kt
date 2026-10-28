package com.friend.ios.widgets

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import kotlin.math.roundToInt

/**
 * `com.omi/screen_brightness`: the phone's screen brightness as a percentage, for the mute-linked
 * levels in Settings > Mute & screen (lib/services/screen_brightness_service.dart).
 *
 * Writing the system brightness needs WRITE_SETTINGS, which only the user can grant on the OS's
 * "Modify system settings" page: `canWrite` says whether they have, `requestPermission` opens that
 * page. `set {percent}` puts the screen in manual mode at that level (never below 1%, so a muted
 * screen stays readable); `get` reads the current level.
 */
object ScreenBrightnessPlugin {
    private const val CHANNEL = "com.omi/screen_brightness"
    private const val MAX = 255

    fun register(activity: Activity, engine: FlutterEngine) {
        val context = activity.applicationContext
        MethodChannel(engine.dartExecutor.binaryMessenger, CHANNEL).setMethodCallHandler { call, result ->
            when (call.method) {
                "canWrite" -> result.success(Settings.System.canWrite(context))
                "requestPermission" -> {
                    val intent = Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS, Uri.parse("package:${context.packageName}"))
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    result.success(runCatching { activity.startActivity(intent) }.isSuccess)
                }
                "get" -> {
                    val raw = runCatching {
                        Settings.System.getInt(context.contentResolver, Settings.System.SCREEN_BRIGHTNESS)
                    }.getOrNull()
                    result.success(raw?.let { (it * 100.0 / MAX).roundToInt().coerceIn(0, 100) })
                }
                "set" -> {
                    val percent = (call.argument<Number>("percent"))?.toInt()
                    if (percent == null || percent < 0 || percent > 100) {
                        result.error("invalid_percent", "percent must be 0..100", null)
                        return@setMethodCallHandler
                    }
                    if (!Settings.System.canWrite(context)) {
                        result.error("permission", "WRITE_SETTINGS not granted", null)
                        return@setMethodCallHandler
                    }
                    val level = (percent * MAX / 100.0).roundToInt().coerceIn(1, MAX)
                    val ok = runCatching {
                        Settings.System.putInt(
                            context.contentResolver,
                            Settings.System.SCREEN_BRIGHTNESS_MODE,
                            Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL,
                        )
                        Settings.System.putInt(context.contentResolver, Settings.System.SCREEN_BRIGHTNESS, level)
                    }.getOrDefault(false)
                    result.success(ok)
                }
                else -> result.notImplemented()
            }
        }
    }
}
