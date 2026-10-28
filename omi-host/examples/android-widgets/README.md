# Android Home Screen widgets and the mute-linked screen

**What.** Five widgets for the phone's Home Screen (long-press the Home Screen →
Widgets → Omi Local), and a setting that ties the phone's brightness to the
capture mute.

| Widget | Shows | Tap |
|---|---|---|
| Mute | *Listening* / *Muted* and the live source | mutes or unmutes capture |
| Live transcript | the last four lines of the conversation being captured | opens the live transcript |
| Day summary | the latest day bundle: title, the start of the text, its tags | opens that day's review |
| Recent | the five newest kept conversations with time and "1 task · 14 s" | opens the conversation |
| To do | the next five open tasks (today first, then soonest due) | opens the task |

Settings → **Mute & screen**: turn *Dim the screen while muted* on, pick the
brightness while muted (default 5%) and while listening (default 60%). Each
time capture goes muted the phone's screen is set to the muted level; each time
it listens again, to the normal level. Your own brightness changes in between
are left alone. Android asks once to let Omi "modify system settings"; the row
on that page opens the OS screen for it.

**Where.**

- `app/android/app/src/main/kotlin/com/friend/ios/widgets/` — the widget
  providers (`OmiMuteWidget`, `OmiTranscriptWidget`, `OmiDaySummaryWidget`,
  `OmiRecentWidget`, `OmiTodoWidget`), the store they draw from
  (`HomeWidgetStore`), the Dart bridge (`HomeWidgetsPlugin`, channel
  `com.omi/home_widgets`) and the brightness bridge (`ScreenBrightnessPlugin`,
  channel `com.omi/screen_brightness`).
- `app/android/app/src/main/res/layout/widget_*.xml`, `res/xml/widget_*_info.xml`,
  `res/values/widget_strings.xml` — the layouts, the launcher descriptors and the
  widget text (Android resources, not ARB: widgets draw without the Flutter engine).
- `app/lib/services/home_widgets_service.dart` — the documents each widget gets
  (`HomeWidgetsPayload.transcript/daySummary/recent/mute`, plus the Up next
  document the iOS widgets already use) and the service that publishes them.
- `app/lib/pages/home/home_widgets_publisher.dart` — when they are published:
  on provider changes (gathered for 0.8 s), on leaving the app, and the day
  summary at most every 30 minutes or on resume.
- `app/lib/services/mute_brightness_link.dart`, `screen_brightness_service.dart`,
  `app/lib/pages/settings/mute_brightness_settings_page.dart` — the brightness.
- Taps open the app through its own links (`omi-dev://app/<route>`, see
  `app/lib/core/app_shell.dart`); `day-review/<date>` and `task/<id>` were added
  for the Day summary and To do widgets.

**Why.** The phone is where you glance, and most of the day Omi is a thing you
mute and unmute. A mute button on the Home Screen, the transcript as it comes
in, the day's summary and the next tasks cover that glance without opening the
app. The screen dimming exists for the same reason: muted means "I am not
using it", so the screen should not shine.

## How the mute button works (and when it cannot)

The app owns the capture state machine, so the widget never flips the mute by
itself. While the app's engine is alive (foreground, or in the background with
Background Mode / Transcribe Later keeping it up) the tap reaches Dart over the
channel and the same code the in-app pause button runs does the work; the
widget redraws when the new state is published. When the app has been closed,
the tap is queued and the app is brought up to apply it; where Android refuses
a background start, the queued tap runs on the next open.

The widget's own text comes from the same durable capture policy native
capture obeys (`flutter.capturePolicy`), so it is right even before the app has
published anything.

## Building

The widgets and the brightness bridge are plain Android code in the app: they
ship in `app/scripts/build_local_apk.sh` with everything else. They were written
on a machine without an Android SDK, so the first build on yours is the first
compile; if Gradle complains, the error names the Kotlin file.
