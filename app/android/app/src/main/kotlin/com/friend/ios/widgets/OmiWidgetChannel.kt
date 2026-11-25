package com.friend.ios.widgets

import android.content.Context
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * The app's widget channel, the same one the iOS widgets use (`com.omi.battery_widget`):
 * `updateWidgetData` stores one document and redraws the widgets that read it.
 */
object OmiWidgetChannel {
    const val NAME = "com.omi.battery_widget"

    fun register(context: Context, engine: FlutterEngine) {
        val app = context.applicationContext
        MethodChannel(engine.dartExecutor.binaryMessenger, NAME).setMethodCallHandler { call, result ->
            when (call.method) {
                "updateWidgetData" -> {
                    val key = call.argument<String>("key")
                    if (key.isNullOrBlank()) {
                        result.error("invalid_widget_key", "key is required", null)
                        return@setMethodCallHandler
                    }
                    OmiWidgetStore.write(app, key, call.argument<String>("json"))
                    OmiWidgetProvider.refresh(app, key)
                    result.success(null)
                }
                "reloadAll" -> {
                    OmiWidgetProvider.refresh(app)
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
    }
}
