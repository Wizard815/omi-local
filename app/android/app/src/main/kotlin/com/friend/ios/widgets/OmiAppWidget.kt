package com.friend.ios.widgets

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.RemoteViews
import com.friend.ios.R

/**
 * Base of every Omi Home Screen widget: one [RemoteViews] drawn from the stored document, redrawn
 * whenever Dart publishes a new one ([refresh]) and on the launcher's own update requests.
 */
abstract class OmiAppWidget : AppWidgetProvider() {
    /** The [HomeWidgetStore] key this widget draws. */
    abstract val key: String

    abstract fun render(context: Context): RemoteViews

    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        val views = render(context)
        for (id in ids) manager.updateAppWidget(id, views)
    }

    companion object {
        private val providers: List<Class<out OmiAppWidget>> = listOf(
            OmiMuteWidget::class.java,
            OmiTranscriptWidget::class.java,
            OmiDaySummaryWidget::class.java,
            OmiRecentWidget::class.java,
            OmiTodoWidget::class.java,
        )

        /** Redraws the widgets that read [key]. */
        fun refresh(context: Context, key: String) {
            for (cls in providers) {
                val provider = cls.getDeclaredConstructor().newInstance()
                if (provider.key == key) redraw(context, cls, provider)
            }
        }

        fun refreshAll(context: Context) {
            for (cls in providers) redraw(context, cls, cls.getDeclaredConstructor().newInstance())
        }

        private fun redraw(context: Context, cls: Class<out OmiAppWidget>, provider: OmiAppWidget) {
            val manager = AppWidgetManager.getInstance(context) ?: return
            val ids = manager.getAppWidgetIds(ComponentName(context, cls))
            if (ids.isEmpty()) return
            provider.onUpdate(context, manager, ids)
        }

        /** Opens the app at one of its own links: `<scheme>://app/<route>` (see core/app_shell.dart). */
        fun openApp(context: Context, route: String, requestCode: Int): PendingIntent {
            val scheme = context.getString(R.string.omi_link_scheme)
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse("$scheme://app/$route")).apply {
                setPackage(context.packageName)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            return PendingIntent.getActivity(
                context, requestCode, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        }

        /** A time of day for a document's unix-seconds stamp, in the phone's zone. */
        fun clock(seconds: Double?): String {
            if (seconds == null || seconds <= 0) return ""
            val fmt = java.text.SimpleDateFormat("HH:mm", java.util.Locale.getDefault())
            return fmt.format(java.util.Date((seconds * 1000).toLong()))
        }
    }
}
