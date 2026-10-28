package com.friend.ios.widgets

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.view.View
import android.widget.RemoteViews
import com.friend.ios.R

/** One big button: whether Omi is listening, and a tap to mute or unmute capture. */
class OmiMuteWidget : OmiAppWidget() {
    override val key = HomeWidgetStore.KEY_MUTE

    override fun render(context: Context): RemoteViews {
        val doc = HomeWidgetStore.read(context, key)
        // Dart's document wins; before it exists the durable capture policy says what native obeys.
        val muted = doc?.optBoolean("muted") ?: HomeWidgetStore.capturePolicyMuted(context) ?: true
        val available = doc?.optBoolean("available", true) ?: true
        val views = RemoteViews(context.packageName, R.layout.widget_mute)
        views.setImageViewResource(R.id.mute_icon, if (muted) R.drawable.ic_widget_mic_off else R.drawable.ic_widget_mic)
        views.setTextViewText(
            R.id.mute_state,
            context.getString(if (muted) R.string.widget_mute_muted else R.string.widget_mute_listening),
        )
        views.setTextViewText(
            R.id.mute_hint,
            context.getString(
                when {
                    !available -> R.string.widget_mute_no_capture
                    muted -> R.string.widget_mute_tap_unmute
                    else -> R.string.widget_mute_tap_mute
                },
            ),
        )
        views.setViewVisibility(R.id.mute_hint, View.VISIBLE)
        val toggle = Intent(context, OmiMuteWidget::class.java).setAction(ACTION_TOGGLE)
        views.setOnClickPendingIntent(
            R.id.mute_root,
            PendingIntent.getBroadcast(
                context, 0, toggle, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            ),
        )
        return views
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == ACTION_TOGGLE) {
            HomeWidgetsPlugin.dispatch(context, HomeWidgetsPlugin.ACTION_TOGGLE_MUTE)
            return
        }
        super.onReceive(context, intent)
    }

    companion object {
        const val ACTION_TOGGLE = "com.friend.ios.widget.TOGGLE_MUTE"
    }
}
