package com.friend.ios.widgets

import android.content.Context
import android.view.View
import android.widget.RemoteViews
import com.friend.ios.R
import org.json.JSONArray

/** Five rows of title + detail from a document's array; a tap on a row opens its item. */
abstract class OmiListWidget : OmiAppWidget() {
    abstract val array: String
    abstract val titleRes: Int
    abstract val emptyRes: Int
    abstract val allRoute: String

    /** The link a row opens, or null for the list's own route. */
    abstract fun rowRoute(item: org.json.JSONObject): String?
    abstract fun rowDetail(context: Context, item: org.json.JSONObject): String

    override fun render(context: Context): RemoteViews {
        val doc = HomeWidgetStore.read(context, key)
        val items: JSONArray? = doc?.optJSONArray(array)
        val views = RemoteViews(context.packageName, R.layout.widget_list)
        views.setTextViewText(R.id.list_title, context.getString(titleRes))
        val count = items?.length() ?: 0
        for ((slot, ids) in ROWS.withIndex()) {
            val (row, title, detail) = ids
            val item = if (slot < count) items?.optJSONObject(slot) else null
            if (item == null) {
                views.setViewVisibility(row, View.GONE)
                continue
            }
            views.setTextViewText(title, item.optString("title"))
            val detailText = rowDetail(context, item)
            views.setTextViewText(detail, detailText)
            views.setViewVisibility(detail, if (detailText.isEmpty()) View.GONE else View.VISIBLE)
            views.setViewVisibility(row, View.VISIBLE)
            views.setOnClickPendingIntent(row, openApp(context, rowRoute(item) ?: allRoute, requestBase + slot))
        }
        views.setViewVisibility(R.id.list_empty, if (count == 0) View.VISIBLE else View.GONE)
        views.setTextViewText(R.id.list_empty, context.getString(emptyRes))
        views.setOnClickPendingIntent(R.id.list_title, openApp(context, allRoute, requestBase + 9))
        views.setOnClickPendingIntent(R.id.list_empty, openApp(context, allRoute, requestBase + 9))
        return views
    }

    protected abstract val requestBase: Int

    companion object {
        private val ROWS = listOf(
            Triple(R.id.row_1, R.id.row_1_title, R.id.row_1_detail),
            Triple(R.id.row_2, R.id.row_2_title, R.id.row_2_detail),
            Triple(R.id.row_3, R.id.row_3_title, R.id.row_3_detail),
            Triple(R.id.row_4, R.id.row_4_title, R.id.row_4_detail),
            Triple(R.id.row_5, R.id.row_5_title, R.id.row_5_detail),
        )
    }
}

/** The newest kept conversations. */
class OmiRecentWidget : OmiListWidget() {
    override val key = HomeWidgetStore.KEY_RECENT
    override val array = "conversations"
    override val titleRes = R.string.widget_recent_title
    override val emptyRes = R.string.widget_recent_none
    override val allRoute = "conversations"
    override val requestBase = 100

    override fun rowRoute(item: org.json.JSONObject): String? =
        item.optString("id").takeIf { it.isNotEmpty() }?.let { "conversation/$it" }

    override fun rowDetail(context: Context, item: org.json.JSONObject): String {
        val at = clock(item.optDouble("at").takeIf { !it.isNaN() })
        val detail = item.optString("detail")
        return listOf(at, detail).filter { it.isNotEmpty() }.joinToString(" · ")
    }
}

/** The next open to-dos (the app's Up next order: today first, then soonest due). */
class OmiTodoWidget : OmiListWidget() {
    override val key = HomeWidgetStore.KEY_UP_NEXT
    override val array = "tasks"
    override val titleRes = R.string.widget_todo_title
    override val emptyRes = R.string.widget_todo_none
    override val allRoute = "action-items"
    override val requestBase = 200

    override fun rowRoute(item: org.json.JSONObject): String? =
        item.optString("id").takeIf { it.isNotEmpty() }?.let { "task/$it" }

    override fun rowDetail(context: Context, item: org.json.JSONObject): String {
        val due = item.optDouble("due")
        if (due.isNaN() || due <= 0) return ""
        val fmt = java.text.SimpleDateFormat("EEE d MMM", java.util.Locale.getDefault())
        return context.getString(R.string.widget_todo_due, fmt.format(java.util.Date((due * 1000).toLong())))
    }
}
