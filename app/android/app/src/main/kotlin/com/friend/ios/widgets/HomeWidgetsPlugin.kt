package com.friend.ios.widgets

import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * `com.omi/home_widgets`: Dart publishes widget documents and asks for the action a widget left
 * while the app was closed; widgets call back into Dart (`widgetAction`) while an engine is alive.
 *
 * Dart -> native: `updateWidgetData {key, json?}` stores the document and redraws that widget;
 * `consumePendingAction` returns the queued action or null; `clear` wipes every document (sign-out).
 * Native -> Dart: `widgetAction {action}` with `toggle_mute`.
 */
object HomeWidgetsPlugin {
    private const val CHANNEL = "com.omi/home_widgets"
    const val ACTION_TOGGLE_MUTE = "toggle_mute"

    @Volatile
    private var channel: MethodChannel? = null
    private val main = Handler(Looper.getMainLooper())

    fun register(context: Context, engine: FlutterEngine) {
        val appContext = context.applicationContext
        val ch = MethodChannel(engine.dartExecutor.binaryMessenger, CHANNEL)
        ch.setMethodCallHandler { call, result ->
            when (call.method) {
                "updateWidgetData" -> {
                    val key = call.argument<String>("key")
                    if (key == null || !HomeWidgetStore.put(appContext, key, call.argument<String>("json"))) {
                        result.error("unknown_widget", "no widget reads $key", null)
                        return@setMethodCallHandler
                    }
                    OmiAppWidget.refresh(appContext, key)
                    result.success(true)
                }
                "consumePendingAction" -> result.success(HomeWidgetStore.consumePendingAction(appContext))
                "clear" -> {
                    HomeWidgetStore.clear(appContext)
                    OmiAppWidget.refreshAll(appContext)
                    result.success(true)
                }
                else -> result.notImplemented()
            }
        }
        channel = ch
    }

    fun unregister() {
        channel = null
    }

    /**
     * A widget asked for [action]. With a live engine Dart handles it now (it owns the capture
     * state machine); otherwise the action waits for the next start, which this opens the app for.
     */
    fun dispatch(context: Context, action: String) {
        val ch = channel
        if (ch != null) {
            main.post { ch.invokeMethod("widgetAction", mapOf("action" to action)) }
            return
        }
        val appContext = context.applicationContext
        HomeWidgetStore.setPendingAction(appContext, action)
        val launch = appContext.packageManager.getLaunchIntentForPackage(appContext.packageName) ?: return
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        // The launcher's tap lets the app come to the front on most builds; where the OS refuses a
        // background start, the action is still queued for the next open.
        runCatching { appContext.startActivity(launch) }
    }
}
