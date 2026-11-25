package com.friend.ios.widgets

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OmiWidgetDataTest {
    @Test
    fun missingOrBrokenDocumentsReadAsNull() {
        assertNull(OmiWidgetData.today(null))
        assertNull(OmiWidgetData.today(""))
        assertNull(OmiWidgetData.today("{not json"))
        assertNull(OmiWidgetData.tasks("{}"))
        assertNull(OmiWidgetData.listening("""{"title":"Listening"}"""))
    }

    @Test
    fun todayCountIsStaleOnAnotherDay() {
        val today = OmiWidgetData.today(
            """{"title":"Today","day":"2026-11-25","conversations":3,"count_label":"3 conversations today",""" +
                """"zero_label":"No conversations today","next_label":"Next"}""",
        )!!
        assertEquals("3 conversations today", today.countLine("2026-11-25"))
        assertEquals("No conversations today", today.countLine("2026-11-26"))
        assertEquals("Next", today.nextLabel)
    }

    @Test
    fun tasksKeepAtMostThreeRowsAndSkipUntitled() {
        val data = OmiWidgetData.tasks(
            """{"title":"Tasks","open":5,"count_label":"5","empty":"All caught up","tasks":[""" +
                """{"id":"a","title":"One","due_label":"Today"},{"id":"b","title":"  "},{"id":"c","title":"Two"},""" +
                """{"id":"d","title":"Three","due_label":null},{"id":"e","title":"Four"}]}""",
        )!!
        assertEquals(5, data.count)
        assertEquals(listOf("One", "Two", "Three"), data.rows.map { it.title })
        assertEquals("Today", data.rows[0].detail)
        assertNull(data.rows[2].detail)
        assertEquals("c", data.rows[1].id)
    }

    @Test
    fun approvalsWithNothingWaiting() {
        val data = OmiWidgetData.approvals(
            """{"title":"Waiting for Your OK","count":0,"count_label":"","empty":"Nothing is waiting for your OK.","items":[]}""",
        )!!
        assertEquals(0, data.count)
        assertTrue(data.rows.isEmpty())
        assertEquals("Nothing is waiting for your OK.", data.empty)
    }

    @Test
    fun listeningAndTranslationStates() {
        val listening = OmiWidgetData.listening(
            """{"title":"Omi","muted":true,"available":true,"state":"Muted","action":"Unmute"}""",
        )!!
        assertTrue(listening.muted)
        assertEquals("Unmute", listening.action)
        val translation = OmiWidgetData.translation(
            """{"title":"Automatic Translation","on":false,"state":"Off","target":"Into English"}""",
        )!!
        assertFalse(translation.on)
        assertEquals("Into English", translation.target)
    }

    @Test
    fun summaryWithoutConversationOpensTheList() {
        val summary = OmiWidgetData.summary("""{"title":"Summary","id":null,"heading":"Standup","text":"Ship Friday"}""")!!
        assertNull(summary.conversationId)
        assertEquals("Standup", summary.heading)
    }

    @Test
    fun mediumSizeStartsAtFourCells() {
        assertFalse(OmiWidgetProvider.isMedium(110))
        assertTrue(OmiWidgetProvider.isMedium(250))
        assertEquals(2, OmiListViews.visibleRows(false))
        assertEquals(3, OmiListViews.visibleRows(true))
    }
}
