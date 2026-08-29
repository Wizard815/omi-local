package com.friend.ios.calendar

import android.content.ContentUris
import android.content.Context
import android.database.Cursor
import android.net.Uri
import android.provider.CalendarContract
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.util.*

/**
 * Reads/creates events from the Android device calendar (CalendarContract).
 * Works with any calendar provider: Google, DAVx5/CalDAV, Exchange, local.
 */
class DeviceCalendarPlugin(private val context: Context) : MethodChannel.MethodCallHandler {

    companion object {
        const val CHANNEL = "com.friend.ios/device_calendar"

        fun registerWith(flutterEngine: io.flutter.embedding.engine.FlutterEngine, context: Context) {
            val channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
            channel.setMethodCallHandler(DeviceCalendarPlugin(context.applicationContext))
        }
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "getCalendars" -> getCalendars(result)
            "getEvents" -> getEvents(call, result)
            "createEvent" -> createEvent(call, result)
            "updateEvent" -> updateEvent(call, result)
            "deleteEvent" -> deleteEvent(call, result)
            else -> result.notImplemented()
        }
    }

    private fun getCalendars(result: MethodChannel.Result) {
        try {
            val calendars = mutableListOf<Map<String, Any?>>()
            val uri = CalendarContract.Calendars.CONTENT_URI
            val projection = arrayOf(
                CalendarContract.Calendars._ID,
                CalendarContract.Calendars.CALENDAR_DISPLAY_NAME,
                CalendarContract.Calendars.ACCOUNT_NAME,
                CalendarContract.Calendars.ACCOUNT_TYPE,
                CalendarContract.Calendars.CALENDAR_COLOR,
                CalendarContract.Calendars.VISIBLE,
                CalendarContract.Calendars.OWNER_ACCOUNT,
            )
            context.contentResolver.query(uri, projection, null, null, null)?.use { cursor ->
                while (cursor.moveToNext()) {
                    calendars.add(mapOf<String, Any?>(
                        "id" to cursor.getLong(0),
                        "name" to cursor.getString(1),
                        "accountName" to cursor.getString(2),
                        "accountType" to cursor.getString(3),
                        "color" to cursor.getInt(4),
                        "visible" to (cursor.getInt(5) == 1),
                        "ownerAccount" to cursor.getString(6),
                    ))
                }
            }
            result.success(calendars)
        } catch (e: SecurityException) {
            result.error("PERMISSION_DENIED", "Calendar permission not granted", null)
        } catch (e: Exception) {
            result.error("CALENDAR_ERROR", e.message, null)
        }
    }

    private fun getEvents(call: MethodCall, result: MethodChannel.Result) {
        try {
            val startMs = call.argument<Long>("startMs") ?: System.currentTimeMillis()
            val endMs = call.argument<Long>("endMs") ?: (startMs + 30L * 24 * 60 * 60 * 1000) // +30 days
            val calendarIds = call.argument<List<Long>>("calendarIds")

            val events = mutableListOf<Map<String, Any?>>()
            val builder = CalendarContract.Instances.CONTENT_URI.buildUpon()
            ContentUris.appendId(builder, startMs)
            ContentUris.appendId(builder, endMs)

            val projection = arrayOf(
                CalendarContract.Instances.EVENT_ID,
                CalendarContract.Instances.TITLE,
                CalendarContract.Instances.BEGIN,
                CalendarContract.Instances.END,
                CalendarContract.Instances.DESCRIPTION,
                CalendarContract.Instances.EVENT_LOCATION,
                CalendarContract.Instances.CALENDAR_ID,
                CalendarContract.Instances.ALL_DAY,
            )

            val selection = if (!calendarIds.isNullOrEmpty()) {
                "${CalendarContract.Instances.CALENDAR_ID} IN (${calendarIds.joinToString(",")})"
            } else null

            context.contentResolver.query(builder.build(), projection, selection, null,
                "${CalendarContract.Instances.BEGIN} ASC")?.use { cursor ->
                while (cursor.moveToNext()) {
                    events.add(mapOf<String, Any?>(
                        "eventId" to cursor.getLong(0),
                        "title" to (cursor.getString(1) ?: "(no title)"),
                        "startMs" to cursor.getLong(2),
                        "endMs" to cursor.getLong(3),
                        "description" to cursor.getString(4),
                        "location" to cursor.getString(5),
                        "calendarId" to cursor.getLong(6),
                        "allDay" to (cursor.getInt(7) == 1),
                    ))
                }
            }
            result.success(events)
        } catch (e: SecurityException) {
            result.error("PERMISSION_DENIED", "Calendar permission not granted", null)
        } catch (e: Exception) {
            result.error("CALENDAR_ERROR", e.message, null)
        }
    }

    private fun createEvent(call: MethodCall, result: MethodChannel.Result) {
        try {
            val calendarId = call.argument<Long>("calendarId") ?: -1L
            val title = call.argument<String>("title") ?: ""
            val startMs = call.argument<Long>("startMs") ?: System.currentTimeMillis()
            val endMs = call.argument<Long>("endMs") ?: (startMs + 3600_000)
            val description = call.argument<String>("description") ?: ""
            val location = call.argument<String>("location") ?: ""
            val allDay = call.argument<Boolean>("allDay") ?: false

            val calId = if (calendarId > 0) calendarId else getDefaultCalendarId()

            val values = android.content.ContentValues().apply {
                put(CalendarContract.Events.CALENDAR_ID, calId)
                put(CalendarContract.Events.TITLE, title)
                put(CalendarContract.Events.DESCRIPTION, description)
                put(CalendarContract.Events.EVENT_LOCATION, location)
                put(CalendarContract.Events.DTSTART, startMs)
                if (allDay) {
                    put(CalendarContract.Events.ALL_DAY, 1)
                    put(CalendarContract.Events.DTEND, endMs)
                } else {
                    put(CalendarContract.Events.DTEND, endMs)
                }
                put(CalendarContract.Events.EVENT_TIMEZONE, TimeZone.getDefault().id)
            }

            val uri = context.contentResolver.insert(CalendarContract.Events.CONTENT_URI, values)
            val eventId = uri?.lastPathSegment?.toLongOrNull() ?: -1L
            result.success(mapOf("eventId" to eventId, "calendarId" to calId))
        } catch (e: SecurityException) {
            result.error("PERMISSION_DENIED", "Calendar permission not granted", null)
        } catch (e: Exception) {
            result.error("CALENDAR_ERROR", e.message, null)
        }
    }

    private fun updateEvent(call: MethodCall, result: MethodChannel.Result) {
        try {
            val eventId = call.argument<Long>("eventId") ?: -1L
            val title = call.argument<String>("title")
            val startMs = call.argument<Long>("startMs")
            val endMs = call.argument<Long>("endMs")
            val description = call.argument<String>("description")

            val values = android.content.ContentValues()
            title?.let { values.put(CalendarContract.Events.TITLE, it) }
            startMs?.let { values.put(CalendarContract.Events.DTSTART, it) }
            endMs?.let { values.put(CalendarContract.Events.DTEND, it) }
            description?.let { values.put(CalendarContract.Events.DESCRIPTION, it) }

            val uri = ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId)
            val updated = context.contentResolver.update(uri, values, null, null)
            result.success(mapOf("updated" to (updated > 0)))
        } catch (e: Exception) {
            result.error("CALENDAR_ERROR", e.message, null)
        }
    }

    private fun deleteEvent(call: MethodCall, result: MethodChannel.Result) {
        try {
            val eventId = call.argument<Long>("eventId") ?: -1L
            val uri = ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId)
            val deleted = context.contentResolver.delete(uri, null, null)
            result.success(mapOf("deleted" to (deleted > 0)))
        } catch (e: Exception) {
            result.error("CALENDAR_ERROR", e.message, null)
        }
    }

    private fun getDefaultCalendarId(): Long {
        val uri = CalendarContract.Calendars.CONTENT_URI
        val projection = arrayOf(CalendarContract.Calendars._ID)
        val selection = "${CalendarContract.Calendars.VISIBLE} = 1 AND ${CalendarContract.Calendars.IS_PRIMARY} = 1"
        context.contentResolver.query(uri, projection, selection, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) return cursor.getLong(0)
        }
        // Fallback to any visible calendar
        context.contentResolver.query(uri, projection,
            "${CalendarContract.Calendars.VISIBLE} = 1", null, null)?.use { cursor ->
            if (cursor.moveToFirst()) return cursor.getLong(0)
        }
        return 1L
    }
}