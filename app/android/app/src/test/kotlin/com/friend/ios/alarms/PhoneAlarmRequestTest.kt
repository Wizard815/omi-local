package com.friend.ios.alarms

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PhoneAlarmRequestTest {
    @Test
    fun alarmRejectsOutOfRangeTimes() {
        assertNull(PhoneAlarmRequest.alarm(24, 0, "x"))
        assertNull(PhoneAlarmRequest.alarm(7, 60, "x"))
        assertNull(PhoneAlarmRequest.alarm(null, 0, "x"))
        assertEquals(PhoneAlarmRequest.Alarm(7, 30, "Call mom"), PhoneAlarmRequest.alarm(7, 30, "  Call mom "))
    }

    @Test
    fun timerIsBetweenOneSecondAndOneDay() {
        assertNull(PhoneAlarmRequest.timer(0, null))
        assertNull(PhoneAlarmRequest.timer(PhoneAlarmRequest.MAX_TIMER_SECONDS + 1, null))
        assertEquals(PhoneAlarmRequest.Timer(600, ""), PhoneAlarmRequest.timer(600, null))
    }

    @Test
    fun longMessagesAreCut() {
        assertEquals(PhoneAlarmRequest.MAX_MESSAGE, PhoneAlarmRequest.clean("a".repeat(500)).length)
    }
}
