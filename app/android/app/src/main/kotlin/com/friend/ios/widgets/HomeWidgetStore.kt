package com.friend.ios.widgets

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONObject

/**
 * The documents the Home Screen widgets draw from, as Dart publishes them through
 * [HomeWidgetsPlugin] (`lib/services/home_widgets_service.dart`): one JSON document per widget
 * kind, kept in a private preferences file so a widget can redraw after the app's process is gone.
 * Also holds the one action a widget asked for while no Flutter engine was alive, which Dart
 * consumes when it next starts.
 */
object HomeWidgetStore {
    private const val FILE = "omi_home_widgets"
    private const val PENDING_ACTION = "pending_action"

    const val KEY_MUTE = "widget_mute"
    const val KEY_TRANSCRIPT = "widget_transcript"
    const val KEY_DAY_SUMMARY = "widget_day_summary"
    const val KEY_RECENT = "widget_recent"
    const val KEY_UP_NEXT = "widget_up_next"

    val keys: Set<String> = setOf(KEY_MUTE, KEY_TRANSCRIPT, KEY_DAY_SUMMARY, KEY_RECENT, KEY_UP_NEXT)

    private fun prefs(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    /** Stores [json] under [key]; null clears it. Returns false for a key no widget reads. */
    fun put(context: Context, key: String, json: String?): Boolean {
        if (key !in keys) return false
        prefs(context).edit().apply {
            if (json == null) remove(key) else putString(key, json)
        }.apply()
        return true
    }

    fun read(context: Context, key: String): JSONObject? {
        val raw = prefs(context).getString(key, null) ?: return null
        return runCatching { JSONObject(raw) }.getOrNull()
    }

    fun clear(context: Context) {
        prefs(context).edit().clear().apply()
    }

    fun setPendingAction(context: Context, action: String) {
        prefs(context).edit().putString(PENDING_ACTION, action).apply()
    }

    /** Returns and forgets the action a widget left for the app. */
    fun consumePendingAction(context: Context): String? {
        val p = prefs(context)
        val action = p.getString(PENDING_ACTION, null) ?: return null
        p.edit().remove(PENDING_ACTION).apply()
        return action
    }

    /**
     * Whether capture is muted, from the same durable policy native capture obeys
     * (`flutter.capturePolicy`, see batch/CaptureAdmissionPolicy.kt), so the Mute widget is right
     * even before Dart has published anything.
     */
    fun capturePolicyMuted(context: Context): Boolean? {
        val raw = context.applicationContext
            .getSharedPreferences("FlutterSharedPreferences", Context.MODE_PRIVATE)
            .getString("flutter.capturePolicy", null) ?: return null
        return com.friend.ios.batch.CaptureAdmissionPolicy.parse(raw)?.muted
    }
}
