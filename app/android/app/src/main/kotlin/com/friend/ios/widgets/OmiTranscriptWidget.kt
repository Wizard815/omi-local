package com.friend.ios.widgets

import android.content.Context
import android.view.View
import android.widget.RemoteViews
import com.friend.ios.R

/** The last lines of the conversation being captured now; a tap opens the live transcript. */
class OmiTranscriptWidget : OmiAppWidget() {
    override val key = HomeWidgetStore.KEY_TRANSCRIPT

    override fun render(context: Context): RemoteViews {
        val doc = HomeWidgetStore.read(context, key)
        val views = RemoteViews(context.packageName, R.layout.widget_transcript)
        val lines = doc?.optJSONArray("lines")
        val recording = doc?.optBoolean("recording") == true
        val rows = intArrayOf(R.id.transcript_1, R.id.transcript_2, R.id.transcript_3, R.id.transcript_4)
        val count = lines?.length() ?: 0
        // Newest at the bottom, like the app's transcript.
        val start = maxOf(0, count - rows.size)
        for ((slot, id) in rows.withIndex()) {
            val i = start + slot
            if (lines == null || i >= count) {
                views.setViewVisibility(id, View.GONE)
                continue
            }
            val line = lines.optJSONObject(i)
            val speaker = line?.optString("speaker").orEmpty()
            val text = line?.optString("text").orEmpty()
            views.setTextViewText(id, if (speaker.isEmpty()) text else "$speaker: $text")
            views.setViewVisibility(id, View.VISIBLE)
        }
        views.setTextViewText(
            R.id.transcript_title,
            context.getString(if (recording) R.string.widget_transcript_live else R.string.widget_transcript_idle),
        )
        views.setViewVisibility(R.id.transcript_empty, if (count == 0) View.VISIBLE else View.GONE)
        views.setTextViewText(
            R.id.transcript_empty,
            context.getString(if (recording) R.string.widget_transcript_waiting else R.string.widget_transcript_none),
        )
        val recordingId = doc?.optString("recording_id").orEmpty()
        val route = if (recording && recordingId.isNotEmpty()) "capture?recording=$recordingId" else "conversations"
        views.setOnClickPendingIntent(R.id.transcript_root, openApp(context, route, 10))
        return views
    }
}
