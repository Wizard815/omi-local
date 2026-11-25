package com.friend.ios.widgets

import android.Manifest
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.provider.CalendarContract
import android.text.format.DateFormat
import android.text.format.DateUtils
import android.view.View
import android.widget.RemoteViews
import com.friend.ios.R
import java.text.SimpleDateFormat
import java.time.LocalDate
import java.util.Date
import java.util.Locale

/**
 * The Android home-screen widgets. Each draws from the document the app last wrote
 * ([OmiWidgetStore]); a tap opens Omi on the matching place. Small and medium sizes share one
 * layout: the medium size shows more rows and lines.
 */
abstract class OmiWidgetProvider : AppWidgetProvider() {
    /** Draws the widget; [medium] is true from about four cells wide. */
    abstract fun render(context: Context, medium: Boolean): RemoteViews

    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        for (id in ids) draw(context, manager, id)
    }

    override fun onAppWidgetOptionsChanged(context: Context, manager: AppWidgetManager, id: Int, options: Bundle) {
        draw(context, manager, id)
    }

    private fun draw(context: Context, manager: AppWidgetManager, id: Int) {
        val width = manager.getAppWidgetOptions(id)?.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0) ?: 0
        manager.updateAppWidget(id, render(context, isMedium(width)))
    }

    companion object {
        const val MEDIUM_MIN_WIDTH_DP = 220

        fun isMedium(minWidthDp: Int): Boolean = minWidthDp >= MEDIUM_MIN_WIDTH_DP

        /** Which widgets draw each document, so a write redraws only those. */
        private val readers: Map<String, List<Class<out OmiWidgetProvider>>> = mapOf(
            OmiWidgetKeys.TODAY to listOf(OmiTodayWidget::class.java),
            OmiWidgetKeys.SUMMARY to listOf(OmiSummaryWidget::class.java),
            OmiWidgetKeys.CAPTURE to listOf(OmiQuickCaptureWidget::class.java),
            OmiWidgetKeys.LISTENING to listOf(OmiListeningWidget::class.java),
            OmiWidgetKeys.TRANSLATION to listOf(OmiTranslationWidget::class.java),
            OmiWidgetKeys.TASKS to listOf(OmiTasksWidget::class.java),
            OmiWidgetKeys.APPROVALS to listOf(OmiApprovalsWidget::class.java),
            OmiWidgetKeys.FAMILY_DAY to listOf(OmiFamilyDayWidget::class.java, OmiNextEventWidget::class.java),
            OmiWidgetKeys.NEXT_EVENT to listOf(OmiNextEventWidget::class.java),
            OmiWidgetKeys.MEMORY to listOf(OmiMemoryWidget::class.java),
        )

        /** Redraws the widgets that read [key], or every widget when [key] is null. */
        fun refresh(context: Context, key: String? = null) {
            val classes = if (key == null) readers.values.flatten().distinct() else readers[key].orEmpty()
            val manager = AppWidgetManager.getInstance(context)
            for (cls in classes) {
                val ids = manager.getAppWidgetIds(ComponentName(context, cls))
                if (ids.isEmpty()) continue
                context.sendBroadcast(
                    Intent(context, cls)
                        .setAction(AppWidgetManager.ACTION_APPWIDGET_UPDATE)
                        .putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, ids),
                )
            }
        }
    }
}

/** Shared drawing for the status layout: a title, one large line, a detail line and an action. */
internal object OmiStatusViews {
    fun build(
        context: Context,
        title: String,
        value: String,
        detail: String?,
        action: String? = null,
        valueLines: Int = 2,
        detailLines: Int = 1,
    ): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.omi_widget_status)
        views.setTextViewText(R.id.omi_widget_title, title)
        views.setTextViewText(R.id.omi_widget_value, value)
        views.setInt(R.id.omi_widget_value, "setMaxLines", valueLines)
        if (detail.isNullOrBlank()) {
            views.setViewVisibility(R.id.omi_widget_detail, View.GONE)
        } else {
            views.setViewVisibility(R.id.omi_widget_detail, View.VISIBLE)
            views.setTextViewText(R.id.omi_widget_detail, detail)
            views.setInt(R.id.omi_widget_detail, "setMaxLines", detailLines)
        }
        if (action.isNullOrBlank()) {
            views.setViewVisibility(R.id.omi_widget_action, View.GONE)
        } else {
            views.setViewVisibility(R.id.omi_widget_action, View.VISIBLE)
            views.setTextViewText(R.id.omi_widget_action, action)
        }
        return views
    }

    /** Before the app has written anything: the widget's name and "Open Omi". */
    fun empty(context: Context, nameRes: Int): RemoteViews {
        val views = build(context, context.getString(nameRes), context.getString(R.string.omi_widget_open), null)
        views.setOnClickPendingIntent(R.id.omi_widget_root, OmiWidgetLinks.open(context, "/conversations", 0))
        return views
    }
}

/** Today: the date, how many conversations today, and the next calendar event when Omi may read it. */
class OmiTodayWidget : OmiWidgetProvider() {
    override fun render(context: Context, medium: Boolean): RemoteViews {
        val data = OmiWidgetData.today(OmiWidgetStore.read(context, OmiWidgetKeys.TODAY))
            ?: return OmiStatusViews.empty(context, R.string.omi_widget_today_name)
        val now = Date()
        val pattern = DateFormat.getBestDateTimePattern(Locale.getDefault(), if (medium) "EEEEMMMMd" else "EEEMMMd")
        val date = SimpleDateFormat(pattern, Locale.getDefault()).format(now)
        val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(now)
        val next = OmiNextEvent.read(context, now.time)?.let { event ->
            val time = DateFormat.getTimeFormat(context).format(Date(event.startMs))
            listOf(data.nextLabel, "$time ${event.title}").filter { it.isNotBlank() }.joinToString(" · ")
        }
        val views = OmiStatusViews.build(context, date, data.countLine(today), next, detailLines = if (medium) 2 else 1)
        views.setOnClickPendingIntent(R.id.omi_widget_root, OmiWidgetLinks.open(context, "/conversations", 1))
        return views
    }
}

/** Summary: the latest conversation's overview. */
class OmiSummaryWidget : OmiWidgetProvider() {
    override fun render(context: Context, medium: Boolean): RemoteViews {
        val data = OmiWidgetData.summary(OmiWidgetStore.read(context, OmiWidgetKeys.SUMMARY))
            ?: return OmiStatusViews.empty(context, R.string.omi_widget_summary_name)
        val views = OmiStatusViews.build(
            context, data.title, data.heading, data.text,
            valueLines = if (medium) 1 else 2, detailLines = if (medium) 4 else 3,
        )
        val route = data.conversationId?.let { "/conversation/${android.net.Uri.encode(it)}" } ?: "/conversations"
        views.setOnClickPendingIntent(R.id.omi_widget_root, OmiWidgetLinks.open(context, route, 2))
        return views
    }
}

/** Quick capture: a new note, and on the medium size a new task too. */
class OmiQuickCaptureWidget : OmiWidgetProvider() {
    override fun render(context: Context, medium: Boolean): RemoteViews {
        val data = OmiWidgetData.capture(OmiWidgetStore.read(context, OmiWidgetKeys.CAPTURE))
        val views = RemoteViews(context.packageName, R.layout.omi_widget_capture)
        views.setTextViewText(R.id.omi_widget_title, data?.title ?: context.getString(R.string.omi_widget_capture_name))
        views.setTextViewText(R.id.omi_widget_note, data?.note ?: context.getString(R.string.omi_widget_capture_note))
        views.setTextViewText(R.id.omi_widget_task, data?.task ?: context.getString(R.string.omi_widget_capture_task))
        views.setViewVisibility(R.id.omi_widget_task, if (medium) View.VISIBLE else View.GONE)
        views.setOnClickPendingIntent(R.id.omi_widget_note, OmiWidgetLinks.open(context, "/new-memory", 3))
        views.setOnClickPendingIntent(R.id.omi_widget_task, OmiWidgetLinks.open(context, "/new-task", 4))
        views.setOnClickPendingIntent(R.id.omi_widget_root, OmiWidgetLinks.open(context, "/new-memory", 5))
        return views
    }
}

/** Mute/unmute: whether Omi is listening; the button pauses or resumes capture in the app. */
class OmiListeningWidget : OmiWidgetProvider() {
    override fun render(context: Context, medium: Boolean): RemoteViews {
        val data = OmiWidgetData.listening(OmiWidgetStore.read(context, OmiWidgetKeys.LISTENING))
            ?: return OmiStatusViews.empty(context, R.string.omi_widget_listening_name)
        val action = if (data.available) data.action else null
        val views = OmiStatusViews.build(context, data.title, data.state, null, action = action, valueLines = 1)
        val route = if (data.muted) "/listening/resume" else "/listening/pause"
        views.setOnClickPendingIntent(R.id.omi_widget_action, OmiWidgetLinks.open(context, route, 6))
        views.setOnClickPendingIntent(R.id.omi_widget_root, OmiWidgetLinks.open(context, "/conversations", 7))
        return views
    }
}

/** Live translation: on or off and into which language; a tap opens the language settings. */
class OmiTranslationWidget : OmiWidgetProvider() {
    override fun render(context: Context, medium: Boolean): RemoteViews {
        val data = OmiWidgetData.translation(OmiWidgetStore.read(context, OmiWidgetKeys.TRANSLATION))
            ?: return OmiStatusViews.empty(context, R.string.omi_widget_translation_name)
        val views = OmiStatusViews.build(context, data.title, data.state, data.target, valueLines = 1)
        views.setOnClickPendingIntent(R.id.omi_widget_root, OmiWidgetLinks.open(context, "/settings/language", 8))
        return views
    }
}

/** Shared drawing for the list layout (tasks, approvals). */
internal object OmiListViews {
    private val rowIds = intArrayOf(R.id.omi_widget_row_1, R.id.omi_widget_row_2, R.id.omi_widget_row_3)
    private val titleIds = intArrayOf(R.id.omi_widget_row_1_title, R.id.omi_widget_row_2_title, R.id.omi_widget_row_3_title)
    private val detailIds = intArrayOf(R.id.omi_widget_row_1_detail, R.id.omi_widget_row_2_detail, R.id.omi_widget_row_3_detail)

    /** Rows the size shows: two on the small size, three on the medium one. */
    fun visibleRows(medium: Boolean): Int = if (medium) OmiWidgetData.MAX_ROWS else 2

    fun build(
        context: Context,
        data: ListData,
        medium: Boolean,
        rowRoute: (WidgetRow) -> String?,
        codeBase: Int,
        footer: String? = null,
    ): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.omi_widget_list)
        views.setViewVisibility(R.id.omi_widget_footer, if (footer.isNullOrBlank()) View.GONE else View.VISIBLE)
        views.setTextViewText(R.id.omi_widget_footer, footer ?: "")
        views.setTextViewText(R.id.omi_widget_title, data.title)
        views.setTextViewText(R.id.omi_widget_count, if (data.count > 0) data.countLabel else "")
        val shown = data.rows.take(visibleRows(medium))
        views.setViewVisibility(R.id.omi_widget_empty, if (shown.isEmpty()) View.VISIBLE else View.GONE)
        views.setTextViewText(R.id.omi_widget_empty, data.empty)
        for (i in rowIds.indices) {
            val row = shown.getOrNull(i)
            if (row == null) {
                views.setViewVisibility(rowIds[i], View.GONE)
                continue
            }
            views.setViewVisibility(rowIds[i], View.VISIBLE)
            views.setTextViewText(titleIds[i], row.title)
            views.setViewVisibility(detailIds[i], if (row.detail.isNullOrBlank()) View.GONE else View.VISIBLE)
            views.setTextViewText(detailIds[i], row.detail ?: "")
            rowRoute(row)?.let { views.setOnClickPendingIntent(rowIds[i], OmiWidgetLinks.open(context, it, codeBase + i)) }
        }
        return views
    }

    fun empty(context: Context, nameRes: Int, route: String, code: Int, title: String? = null): RemoteViews {
        val views = RemoteViews(context.packageName, R.layout.omi_widget_list)
        views.setTextViewText(R.id.omi_widget_title, title ?: context.getString(nameRes))
        views.setViewVisibility(R.id.omi_widget_footer, View.GONE)
        views.setTextViewText(R.id.omi_widget_count, "")
        views.setViewVisibility(R.id.omi_widget_empty, View.VISIBLE)
        views.setTextViewText(R.id.omi_widget_empty, context.getString(R.string.omi_widget_open))
        for (id in rowIds) views.setViewVisibility(id, View.GONE)
        views.setOnClickPendingIntent(R.id.omi_widget_root, OmiWidgetLinks.open(context, route, code))
        return views
    }
}

/** Task list: the next open tasks; a row opens that task, the rest of the widget opens Tasks. */
class OmiTasksWidget : OmiWidgetProvider() {
    override fun render(context: Context, medium: Boolean): RemoteViews {
        val data = OmiWidgetData.tasks(OmiWidgetStore.read(context, OmiWidgetKeys.TASKS))
            ?: return OmiListViews.empty(context, R.string.omi_widget_tasks_name, "/action-items", 10)
        val views = OmiListViews.build(context, data, medium, { row -> row.id?.let { "/task/${android.net.Uri.encode(it)}" } }, 11)
        views.setOnClickPendingIntent(R.id.omi_widget_root, OmiWidgetLinks.open(context, "/action-items", 10))
        return views
    }
}

/** Approval list: how much waits for the user's OK and the first items; a tap opens Approvals. */
class OmiApprovalsWidget : OmiWidgetProvider() {
    override fun render(context: Context, medium: Boolean): RemoteViews {
        val data = OmiWidgetData.approvals(OmiWidgetStore.read(context, OmiWidgetKeys.APPROVALS))
            ?: return OmiListViews.empty(context, R.string.omi_widget_approvals_name, "/approvals", 20)
        val views = OmiListViews.build(context, data, medium, { "/approvals" }, 21)
        views.setOnClickPendingIntent(R.id.omi_widget_root, OmiWidgetLinks.open(context, "/approvals", 20))
        return views
    }
}

/**
 * Family day: what is still ahead today on the family calendar, then tasks due and overdue
 * chores. Rows from an earlier day are dropped until the app writes today's.
 */
class OmiFamilyDayWidget : OmiWidgetProvider() {
    override fun render(context: Context, medium: Boolean): RemoteViews {
        val data = OmiWidgetData.familyDay(OmiWidgetStore.read(context, OmiWidgetKeys.FAMILY_DAY))
        val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
        if (data == null || data.day != today) {
            return OmiListViews.empty(context, R.string.omi_widget_family_day_name, "/conversations", 30, data?.title)
        }
        val views = OmiListViews.build(context, data.list, medium, { null }, 31, footer = data.footer)
        views.setOnClickPendingIntent(R.id.omi_widget_root, OmiWidgetLinks.open(context, "/conversations", 30))
        return views
    }
}

/**
 * Next event: the soonest of the phone's next event (when Omi may read the calendars) and the
 * family calendar's next timed block. A phone event opens the calendar app at that time.
 */
class OmiNextEventWidget : OmiWidgetProvider() {
    override fun render(context: Context, medium: Boolean): RemoteViews {
        val labels = OmiWidgetData.nextEventLabels(OmiWidgetStore.read(context, OmiWidgetKeys.NEXT_EVENT))
            ?: return OmiStatusViews.empty(context, R.string.omi_widget_next_event_name)
        val now = System.currentTimeMillis()
        val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date(now))
        val phone = OmiNextEvent.read(context, now)?.let { TimedEvent(it.title, it.startMs) }
        val family = OmiWidgetData.nextFamilyEvent(
            OmiWidgetData.familyDay(OmiWidgetStore.read(context, OmiWidgetKeys.FAMILY_DAY)), today, now,
        )
        val picked = OmiWidgetData.soonest(phone, family)
        if (picked == null) {
            val views = OmiStatusViews.build(context, labels.title, labels.none, null, valueLines = 2)
            views.setOnClickPendingIntent(R.id.omi_widget_root, OmiWidgetLinks.open(context, "/conversations", 40))
            return views
        }
        val (event, fromFamily) = picked
        var flags = DateUtils.FORMAT_SHOW_TIME
        if (!DateUtils.isToday(event.startMs)) flags = flags or DateUtils.FORMAT_SHOW_WEEKDAY or DateUtils.FORMAT_ABBREV_WEEKDAY
        val time = DateUtils.formatDateTime(context, event.startMs, flags)
        val detail = if (fromFamily && labels.familyLabel.isNotBlank()) "$time · ${labels.familyLabel}" else time
        val views = OmiStatusViews.build(
            context, labels.title, event.title, detail, valueLines = if (medium) 2 else 3, detailLines = 1,
        )
        val tap = if (fromFamily) OmiWidgetLinks.open(context, "/conversations", 40) else OmiNextEvent.openAt(context, event.startMs, 41)
        views.setOnClickPendingIntent(R.id.omi_widget_root, tap)
        return views
    }
}

/** Memory: one of the user's memories, a different one each day; a tap opens it in Omi. */
class OmiMemoryWidget : OmiWidgetProvider() {
    override fun render(context: Context, medium: Boolean): RemoteViews {
        val data = OmiWidgetData.memory(OmiWidgetStore.read(context, OmiWidgetKeys.MEMORY))
            ?: return OmiStatusViews.empty(context, R.string.omi_widget_memory_name)
        val item = OmiWidgetData.memoryOfDay(data, LocalDate.now().toEpochDay())
        val views = OmiStatusViews.build(
            context, data.title, item?.text ?: data.empty, null, valueLines = if (medium) 4 else 5,
        )
        val route = item?.id?.let { "/memory/${android.net.Uri.encode(it)}" } ?: "/new-memory"
        views.setOnClickPendingIntent(R.id.omi_widget_root, OmiWidgetLinks.open(context, route, 50))
        return views
    }
}

/** The next event in the phone's calendars within a day, only when Omi already may read them. */
internal object OmiNextEvent {
    data class Event(val title: String, val startMs: Long)

    private const val WINDOW_MS = 24L * 60 * 60 * 1000

    /** Opens the phone's calendar app at [timeMs]. */
    fun openAt(context: Context, timeMs: Long, requestCode: Int): android.app.PendingIntent {
        val uri = CalendarContract.CONTENT_URI.buildUpon().appendPath("time").appendPath(timeMs.toString()).build()
        val intent = Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return android.app.PendingIntent.getActivity(
            context,
            requestCode,
            intent,
            android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE,
        )
    }

    fun read(context: Context, nowMs: Long): Event? {
        if (context.checkSelfPermission(Manifest.permission.READ_CALENDAR) != PackageManager.PERMISSION_GRANTED) return null
        val uri = CalendarContract.Instances.CONTENT_URI.buildUpon()
            .appendPath(nowMs.toString())
            .appendPath((nowMs + WINDOW_MS).toString())
            .build()
        val columns = arrayOf(CalendarContract.Instances.TITLE, CalendarContract.Instances.BEGIN, CalendarContract.Instances.ALL_DAY)
        return try {
            context.contentResolver.query(uri, columns, null, null, "${CalendarContract.Instances.BEGIN} ASC")?.use { cursor ->
                while (cursor.moveToNext()) {
                    val begin = cursor.getLong(1)
                    if (cursor.getInt(2) == 1 || begin < nowMs) continue
                    val title = cursor.getString(0)?.trim().orEmpty()
                    if (title.isNotEmpty()) return@use Event(title, begin)
                }
                null
            }
        } catch (_: Exception) {
            null
        }
    }
}
