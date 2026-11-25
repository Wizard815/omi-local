package com.friend.ios.widgets

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class OmiAiSourceDataTest {
    @Test
    fun readsModeStatusAndCheckedLine() {
        val data = OmiWidgetData.aiSource(
            """{"title":"AI source","mode":"auto","mode_label":"Auto","status":"loading",""" +
                """"status_label":"Phone model loading","checked_at":1764090000000,"checked_label":"Checked 10:43",""" +
                """"action":"Switch"}""",
        )!!
        assertEquals("auto", data.mode)
        assertEquals("Auto", data.modeLabel)
        assertEquals("loading", data.status)
        assertEquals("Phone model loading · Checked 10:43", data.detail)
        assertEquals("Switch", data.action)
    }

    @Test
    fun notCheckedYetShowsOnlyTheState() {
        val data = OmiWidgetData.aiSource(
            """{"title":"AI source","mode":"server","mode_label":"Server","status":"unknown",""" +
                """"status_label":"Phone model off","checked_at":null,"checked_label":"","action":"Switch"}""",
        )!!
        assertEquals("Phone model off", data.detail)
    }

    @Test
    fun unknownStatusReadsAsUnknown() {
        val data = OmiWidgetData.aiSource("""{"mode":"phone","mode_label":"This phone","status":"melting"}""")!!
        assertEquals("unknown", data.status)
        assertEquals("", data.title)
    }

    @Test
    fun missingOrUnknownModeReadsAsNull() {
        assertNull(OmiWidgetData.aiSource(null))
        assertNull(OmiWidgetData.aiSource("{broken"))
        assertNull(OmiWidgetData.aiSource("""{"mode_label":"Server"}"""))
        assertNull(OmiWidgetData.aiSource("""{"mode":"cloud","mode_label":"Cloud"}"""))
        assertNull(OmiWidgetData.aiSource("""{"mode":"server"}"""))
    }
}
