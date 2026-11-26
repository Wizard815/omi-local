package com.friend.ios.widgets

import org.json.JSONArray
import org.json.JSONObject

/**
 * The documents the app writes for the home-screen widgets (lib/services/home_widgets_service.dart),
 * as plain values. Parsing is pure so it is checked without a phone; a missing or malformed
 * document reads as null and the widget asks to open Omi. Every label arrives already localized.
 */
object OmiWidgetKeys {
    const val TODAY = "widget_today"
    const val SUMMARY = "widget_summary"
    const val CAPTURE = "widget_capture"
    const val LISTENING = "widget_listening"
    const val TRANSLATION = "widget_translation"
    const val TASKS = "widget_up_next"
    const val APPROVALS = "widget_approvals"
    const val FAMILY_DAY = "widget_family_day"
    const val NEXT_EVENT = "widget_next_event"
    const val MEMORY = "widget_memory"
    const val AI_SOURCE = "widget_ai_source"
}

data class TodayData(
    val title: String,
    /** The local day the count belongs to, `yyyy-MM-dd`. */
    val day: String,
    val conversations: Int,
    val countLabel: String,
    val zeroLabel: String,
    val nextLabel: String,
) {
    /** The count line for [today]: a count from another day is stale and reads as none. */
    fun countLine(today: String): String = if (day == today && conversations > 0) countLabel else zeroLabel
}

data class SummaryData(val title: String, val conversationId: String?, val heading: String, val text: String)

data class CaptureData(val title: String, val note: String, val task: String)

data class ListeningData(val title: String, val muted: Boolean, val available: Boolean, val state: String, val action: String)

data class TranslationData(val title: String, val on: Boolean, val state: String, val target: String)

/**
 * Where chat is answered (`mode`: server, phone or auto) and the phone model's last known state
 * (`status`: running, loading, off or unknown), with the app's "Checked 10:43" line.
 */
data class AiSourceData(
    val title: String,
    val mode: String,
    val modeLabel: String,
    val status: String,
    val statusLabel: String,
    val checkedLabel: String,
    val action: String,
) {
    /** The detail line: the model's state and, once checked, when. */
    val detail: String get() = listOf(statusLabel, checkedLabel).filter { it.isNotBlank() }.joinToString(" · ")
}

data class WidgetRow(val id: String?, val title: String, val detail: String?)

data class ListData(val title: String, val count: Int, val countLabel: String, val empty: String, val rows: List<WidgetRow>)

/** A timed block on the family calendar, for the Next event widget. */
data class TimedEvent(val title: String, val startMs: Long)

data class FamilyDayData(
    val title: String,
    /** The day the rows belong to, `yyyy-MM-dd`; rows from another day are stale. */
    val day: String,
    val configured: Boolean,
    val list: ListData,
    /** Tasks due and overdue chores, one line; empty when none. */
    val footer: String,
    val events: List<TimedEvent>,
)

data class NextEventLabels(val title: String, val none: String, val familyLabel: String)

data class MemoryItem(val id: String?, val text: String)

data class MemoryData(val title: String, val empty: String, val memories: List<MemoryItem>)

object OmiWidgetData {
    /** How many rows a widget lists at most (the medium size). */
    const val MAX_ROWS = 3

    fun today(json: String?): TodayData? = parse(json) { o ->
        TodayData(
            title = o.text("title") ?: return@parse null,
            day = o.text("day") ?: return@parse null,
            conversations = o.optInt("conversations", 0).coerceAtLeast(0),
            countLabel = o.text("count_label") ?: "",
            zeroLabel = o.text("zero_label") ?: "",
            nextLabel = o.text("next_label") ?: "",
        )
    }

    fun summary(json: String?): SummaryData? = parse(json) { o ->
        val heading = o.text("heading") ?: return@parse null
        SummaryData(o.text("title") ?: "", o.text("id"), heading, o.text("text") ?: "")
    }

    fun capture(json: String?): CaptureData? = parse(json) { o ->
        CaptureData(o.text("title") ?: "", o.text("note") ?: return@parse null, o.text("task") ?: return@parse null)
    }

    fun listening(json: String?): ListeningData? = parse(json) { o ->
        ListeningData(
            title = o.text("title") ?: "",
            muted = o.optBoolean("muted", false),
            available = o.optBoolean("available", false),
            state = o.text("state") ?: return@parse null,
            action = o.text("action") ?: "",
        )
    }

    fun translation(json: String?): TranslationData? = parse(json) { o ->
        TranslationData(
            title = o.text("title") ?: "",
            on = o.optBoolean("on", false),
            state = o.text("state") ?: return@parse null,
            target = o.text("target") ?: "",
        )
    }

    /** The task list: the iOS Up next document plus the labels the Android widget shows. */
    fun tasks(json: String?): ListData? = parse(json) { o ->
        val rows = rows(o.optJSONArray("tasks")) { item ->
            val title = item.text("title") ?: return@rows null
            WidgetRow(item.text("id"), title, item.text("due_label"))
        }
        ListData(
            title = o.text("title") ?: return@parse null,
            count = o.optInt("open", rows.size).coerceAtLeast(0),
            countLabel = o.text("count_label") ?: "",
            empty = o.text("empty") ?: "",
            rows = rows,
        )
    }

    fun approvals(json: String?): ListData? = parse(json) { o ->
        val rows = rows(o.optJSONArray("items")) { item ->
            val title = item.text("title") ?: return@rows null
            WidgetRow(null, title, item.text("detail"))
        }
        ListData(
            title = o.text("title") ?: return@parse null,
            count = o.optInt("count", rows.size).coerceAtLeast(0),
            countLabel = o.text("count_label") ?: "",
            empty = o.text("empty") ?: "",
            rows = rows,
        )
    }

    fun familyDay(json: String?): FamilyDayData? = parse(json) { o ->
        val title = o.text("title") ?: return@parse null
        val rows = rows(o.optJSONArray("rows")) { item ->
            WidgetRow(null, item.text("title") ?: return@rows null, item.text("detail"))
        }
        val events = mutableListOf<TimedEvent>()
        o.optJSONArray("events")?.let { array ->
            for (i in 0 until array.length()) {
                val item = array.optJSONObject(i) ?: continue
                val start = item.optLong("start_ms", 0L)
                val name = item.text("title") ?: continue
                if (start > 0) events.add(TimedEvent(name, start))
            }
        }
        FamilyDayData(
            title = title,
            day = o.text("day") ?: return@parse null,
            configured = o.optBoolean("configured", false),
            list = ListData(
                title = title,
                count = o.optInt("count", rows.size).coerceAtLeast(0),
                countLabel = o.text("count_label") ?: "",
                empty = o.text("empty") ?: "",
                rows = rows,
            ),
            footer = o.text("footer") ?: "",
            events = events.sortedBy { it.startMs },
        )
    }

    fun nextEventLabels(json: String?): NextEventLabels? = parse(json) { o ->
        NextEventLabels(o.text("title") ?: return@parse null, o.text("none") ?: "", o.text("family_label") ?: "")
    }

    fun memory(json: String?): MemoryData? = parse(json) { o ->
        val items = mutableListOf<MemoryItem>()
        o.optJSONArray("memories")?.let { array ->
            for (i in 0 until array.length()) {
                val item = array.optJSONObject(i) ?: continue
                val text = item.text("text") ?: continue
                items.add(MemoryItem(item.text("id"), text))
            }
        }
        MemoryData(o.text("title") ?: return@parse null, o.text("empty") ?: "", items)
    }

    /** The family calendar's next timed block from [nowMs] on, only from today's document. */
    fun nextFamilyEvent(data: FamilyDayData?, today: String, nowMs: Long): TimedEvent? =
        data?.takeIf { it.day == today }?.events?.firstOrNull { it.startMs >= nowMs }

    /**
     * The sooner of the phone's next event and the family calendar's; the phone's wins a tie (a
     * family calendar synced to the phone shows up in both).
     */
    fun soonest(phone: TimedEvent?, family: TimedEvent?): Pair<TimedEvent, Boolean>? = when {
        phone == null && family == null -> null
        family == null -> phone!! to false
        phone == null -> family to true
        family.startMs < phone.startMs -> family to true
        else -> phone to false
    }

    /** Today's memory: a different one each local day, in a fixed order, so it turns without the app. */
    fun memoryOfDay(data: MemoryData, epochDay: Long): MemoryItem? {
        if (data.memories.isEmpty()) return null
        return data.memories[Math.floorMod(epochDay, data.memories.size.toLong()).toInt()]
    }
    fun aiSource(json: String?): AiSourceData? = parse(json) { o ->
        val mode = o.text("mode")?.takeIf { it in AI_MODES } ?: return@parse null
        AiSourceData(
            title = o.text("title") ?: "",
            mode = mode,
            modeLabel = o.text("mode_label") ?: return@parse null,
            status = o.text("status")?.takeIf { it in AI_STATUSES } ?: "unknown",
            statusLabel = o.text("status_label") ?: "",
            checkedLabel = o.text("checked_label") ?: "",
            action = o.text("action") ?: "",
        )
    }

    private val AI_MODES = setOf("server", "phone", "auto")
    private val AI_STATUSES = setOf("running", "loading", "off", "unknown")

    private fun <T> parse(json: String?, read: (JSONObject) -> T?): T? {
        if (json.isNullOrBlank()) return null
        return try {
            read(JSONObject(json))
        } catch (_: Exception) {
            null
        }
    }

    private fun rows(array: JSONArray?, read: (JSONObject) -> WidgetRow?): List<WidgetRow> {
        if (array == null) return emptyList()
        val out = mutableListOf<WidgetRow>()
        for (i in 0 until array.length()) {
            val item = array.optJSONObject(i) ?: continue
            read(item)?.let(out::add)
            if (out.size == MAX_ROWS) break
        }
        return out
    }

    /** A non-blank string, or null (JSON null reads as null, not "null"). */
    private fun JSONObject.text(key: String): String? {
        if (!has(key) || isNull(key)) return null
        return optString(key).trim().takeIf { it.isNotEmpty() }
    }
}
