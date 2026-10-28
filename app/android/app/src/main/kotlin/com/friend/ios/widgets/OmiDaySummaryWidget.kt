package com.friend.ios.widgets

import android.content.Context
import android.view.View
import android.widget.RemoteViews
import com.friend.ios.R

/** The latest day summary (the day bundle): its title, the first of its text and its tags. */
class OmiDaySummaryWidget : OmiAppWidget() {
    override val key = HomeWidgetStore.KEY_DAY_SUMMARY

    override fun render(context: Context): RemoteViews {
        val doc = HomeWidgetStore.read(context, key)
        val views = RemoteViews(context.packageName, R.layout.widget_day_summary)
        val date = doc?.optString("date").orEmpty()
        val title = doc?.optString("title").orEmpty()
        val text = doc?.optString("text").orEmpty()
        val tags = doc?.optJSONArray("tags")
        views.setTextViewText(R.id.summary_date, if (date.isEmpty()) context.getString(R.string.widget_day_summary_label) else date)
        views.setTextViewText(R.id.summary_title, if (title.isEmpty()) context.getString(R.string.widget_day_summary_none) else title)
        views.setTextViewText(R.id.summary_text, text)
        views.setViewVisibility(R.id.summary_text, if (text.isEmpty()) View.GONE else View.VISIBLE)
        val tagLine = (0 until (tags?.length() ?: 0)).mapNotNull { tags?.optString(it) }.filter { it.isNotEmpty() }
            .joinToString("  ") { "#$it" }
        views.setTextViewText(R.id.summary_tags, tagLine)
        views.setViewVisibility(R.id.summary_tags, if (tagLine.isEmpty()) View.GONE else View.VISIBLE)
        val route = if (date.isEmpty()) "conversations" else "day-review/$date"
        views.setOnClickPendingIntent(R.id.summary_root, openApp(context, route, 20))
        return views
    }
}
