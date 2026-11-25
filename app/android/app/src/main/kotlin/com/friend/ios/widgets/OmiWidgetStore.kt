package com.friend.ios.widgets

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import com.friend.ios.MainActivity

/** Where the widget documents live: app-private preferences, written only by the app itself. */
object OmiWidgetStore {
    private const val PREFS = "omi_home_widgets"

    fun read(context: Context, key: String): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(key, null)

    /** Stores [json] under [key]; null removes it (signing out leaves no titles on the home screen). */
    fun write(context: Context, key: String, json: String?) {
        val edit = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
        if (json == null) edit.remove(key) else edit.putString(key, json)
        edit.apply()
    }
}

/**
 * Taps open Omi on the route the app's own widget links use (`<scheme>://app/<route>`, handled in
 * lib/core/app_shell.dart). The intent names MainActivity directly, so it works in every flavor.
 */
object OmiWidgetLinks {
    fun route(route: String): Uri = Uri.parse("omi://app" + (if (route.startsWith("/")) route else "/$route"))

    fun open(context: Context, route: String, requestCode: Int): PendingIntent {
        val intent = Intent(Intent.ACTION_VIEW, route(route), context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        return PendingIntent.getActivity(
            context,
            requestCode,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}
