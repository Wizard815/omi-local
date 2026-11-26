package com.friend.ios.alarms

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.provider.AlarmClock
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/** A validated clock-app request: an alarm at [hour]:[minute], or a timer of [seconds]. */
sealed class PhoneAlarmRequest {
    data class Alarm(val hour: Int, val minute: Int, val message: String) : PhoneAlarmRequest()
    data class Timer(val seconds: Int, val message: String) : PhoneAlarmRequest()

    companion object {
        const val MAX_TIMER_SECONDS = 24 * 60 * 60
        const val MAX_MESSAGE = 120

        /** Null for an out-of-range value; the clock app is never opened on a guess. */
        fun alarm(hour: Int?, minute: Int?, message: String?): Alarm? {
            if (hour == null || minute == null || hour !in 0..23 || minute !in 0..59) return null
            return Alarm(hour, minute, clean(message))
        }

        fun timer(seconds: Int?, message: String?): Timer? {
            if (seconds == null || seconds !in 1..MAX_TIMER_SECONDS) return null
            return Timer(seconds, clean(message))
        }

        fun clean(message: String?): String = message.orEmpty().trim().take(MAX_MESSAGE)
    }
}

/**
 * `com.omi/phone_alarm` (lib/services/reminders/phone_alarm.dart): opens the phone's clock app on
 * a new alarm or timer with the values filled in. EXTRA_SKIP_UI is false, so the user sees it and
 * saves it in the clock app; Omi never sets one silently.
 */
object PhoneAlarmPlugin {
    const val CHANNEL = "com.omi/phone_alarm"

    fun register(engine: FlutterEngine, activity: Activity) {
        MethodChannel(engine.dartExecutor.binaryMessenger, CHANNEL).setMethodCallHandler { call, result ->
            val request: PhoneAlarmRequest? = when (call.method) {
                "setAlarm" -> PhoneAlarmRequest.alarm(call.argument<Int>("hour"), call.argument<Int>("minute"), call.argument<String>("message"))
                "setTimer" -> PhoneAlarmRequest.timer(call.argument<Int>("seconds"), call.argument<String>("message"))
                else -> {
                    result.notImplemented()
                    return@setMethodCallHandler
                }
            }
            if (request == null) {
                result.success(mapOf("ok" to false, "reason" to "invalid_arguments", "error" to "Values out of range."))
                return@setMethodCallHandler
            }
            try {
                activity.startActivity(intentFor(request))
                result.success(mapOf("ok" to true, "status" to "opened"))
            } catch (_: ActivityNotFoundException) {
                result.success(mapOf("ok" to false, "reason" to "no_clock_app", "error" to "No clock app can set this."))
            } catch (e: SecurityException) {
                result.success(mapOf("ok" to false, "reason" to "platform_error", "error" to (e.message ?: "Not allowed.")))
            }
        }
    }

    fun intentFor(request: PhoneAlarmRequest): Intent = when (request) {
        is PhoneAlarmRequest.Alarm -> Intent(AlarmClock.ACTION_SET_ALARM)
            .putExtra(AlarmClock.EXTRA_HOUR, request.hour)
            .putExtra(AlarmClock.EXTRA_MINUTES, request.minute)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, false)
            .apply { if (request.message.isNotEmpty()) putExtra(AlarmClock.EXTRA_MESSAGE, request.message) }
        is PhoneAlarmRequest.Timer -> Intent(AlarmClock.ACTION_SET_TIMER)
            .putExtra(AlarmClock.EXTRA_LENGTH, request.seconds)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, false)
            .apply { if (request.message.isNotEmpty()) putExtra(AlarmClock.EXTRA_MESSAGE, request.message) }
    }
}
