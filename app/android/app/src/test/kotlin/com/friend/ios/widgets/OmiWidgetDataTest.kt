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

    private val familyJson =
        """{"title":"Family day","day":"2026-11-25","configured":true,"count":4,"count_label":"4 left",""" +
            """"empty":"Nothing else on the calendar today","footer":"2 tasks due today",""" +
            """"rows":[{"title":"Swim","detail":"All day"},{"title":"  "},{"title":"Dentist","detail":"14:30"},""" +
            """{"title":"Pickup","detail":"15:15"},{"title":"Dinner","detail":"18:00"}],""" +
            """"events":[{"title":"Pickup","start_ms":3000},{"title":"Dentist","start_ms":2000},{"title":"","start_ms":2500}]}"""

    @Test
    fun familyDayKeepsThreeRowsAndSortsTimedEvents() {
        val day = OmiWidgetData.familyDay(familyJson)!!
        assertEquals(listOf("Swim", "Dentist", "Pickup"), day.list.rows.map { it.title })
        assertEquals("4 left", day.list.countLabel)
        assertEquals("2 tasks due today", day.footer)
        assertEquals(listOf("Dentist", "Pickup"), day.events.map { it.title })
        assertNull(OmiWidgetData.familyDay("""{"title":"Family day"}"""))
    }

    @Test
    fun nextFamilyEventSkipsPassedBlocksAndOtherDays() {
        val day = OmiWidgetData.familyDay(familyJson)
        assertEquals("Dentist", OmiWidgetData.nextFamilyEvent(day, "2026-11-25", 1500)?.title)
        assertEquals("Pickup", OmiWidgetData.nextFamilyEvent(day, "2026-11-25", 2001)?.title)
        assertNull(OmiWidgetData.nextFamilyEvent(day, "2026-11-25", 3001))
        assertNull(OmiWidgetData.nextFamilyEvent(day, "2026-11-26", 0))
        assertNull(OmiWidgetData.nextFamilyEvent(null, "2026-11-25", 0))
    }

    @Test
    fun soonestPrefersThePhoneOnATie() {
        val phone = TimedEvent("Phone", 2000)
        val family = TimedEvent("Family", 2000)
        assertEquals(phone to false, OmiWidgetData.soonest(phone, family))
        assertEquals(TimedEvent("Early", 1000) to true, OmiWidgetData.soonest(phone, TimedEvent("Early", 1000)))
        assertEquals(family to true, OmiWidgetData.soonest(null, family))
        assertNull(OmiWidgetData.soonest(null, null))
    }

    @Test
    fun memoryTurnsOncePerDayInAFixedOrder() {
        val data = OmiWidgetData.memory(
            """{"title":"Memory","empty":"No memories yet","memories":[{"id":"a","text":"One"},""" +
                """{"id":"b","text":" "},{"id":"c","text":"Two"},{"text":"Three"}]}""",
        )!!
        assertEquals(listOf("One", "Two", "Three"), data.memories.map { it.text })
        assertEquals("One", OmiWidgetData.memoryOfDay(data, 0)?.text)
        assertEquals("Two", OmiWidgetData.memoryOfDay(data, 1)?.text)
        assertEquals("One", OmiWidgetData.memoryOfDay(data, 3)?.text)
        assertEquals("Three", OmiWidgetData.memoryOfDay(data, -1)?.text)
        assertNull(OmiWidgetData.memoryOfDay(data, 2)?.id)
        assertNull(OmiWidgetData.memoryOfDay(OmiWidgetData.memory("""{"title":"Memory","memories":[]}""")!!, 5))
    }

    @Test
    fun nextEventLabelsNeedATitle() {
        assertNull(OmiWidgetData.nextEventLabels("""{"none":"Nothing"}"""))
        val labels = OmiWidgetData.nextEventLabels("""{"title":"Next event","none":"Nothing","family_label":"Family calendar"}""")!!
        assertEquals("Family calendar", labels.familyLabel)
    }
}
