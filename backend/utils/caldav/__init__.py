"""CalDAV for the self-hosted stack: tasks as VTODO, days and conversation
events as VEVENT, and the user's calendar pulled back for the chat tools.

ical.py   renders and parses the few iCalendar shapes we exchange
client.py a minimal CalDAV client over httpx (discover, list, PUT, REPORT)
sync.py   one user's sync, driven by routers/caldav.py and the hourly scheduler
"""
