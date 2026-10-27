"""A minimal CalDAV client over httpx: enough for Radicale, Nextcloud, Baïkal,
iCloud and DAVx5-served calendars. Discovery follows RFC 6764/4791
(current-user-principal → calendar-home-set → calendars), objects are read
with calendar-query REPORTs and written with PUT.

Every network error surfaces as CalDavError with the HTTP status when there
is one; the sync layer decides what is retried.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import httpx

NS = {'d': 'DAV:', 'c': 'urn:ietf:params:xml:ns:caldav'}
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class CalDavError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


@dataclass
class Calendar:
    href: str
    name: str
    components: List[str] = field(default_factory=list)  # e.g. ['VEVENT', 'VTODO']

    @property
    def url(self) -> str:
        return self.href

    def supports(self, component: str) -> bool:
        return not self.components or component.upper() in self.components


@dataclass
class CalendarObject:
    href: str
    etag: str
    data: str


def _absolute(base: str, href: str) -> str:
    """Server hrefs are usually root-relative paths; make them absolute on the base's host."""
    if href.startswith('http://') or href.startswith('https://'):
        return href
    parsed = urlparse(base)
    return urlunparse((parsed.scheme, parsed.netloc, href, '', '', ''))


class CalDavClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: httpx.Timeout = _TIMEOUT,
    ):
        self.base_url = base_url if base_url.endswith('/') else base_url + '/'
        self._http = httpx.Client(
            auth=(username, password), transport=transport, timeout=timeout, follow_redirects=True, trust_env=False
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> 'CalDavClient':
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- raw methods -----------------------------------------------------------------
    def _request(
        self, method: str, url: str, *, body: Optional[str] = None, headers: Optional[Dict[str, str]] = None
    ) -> httpx.Response:
        try:
            response = self._http.request(
                method, url, content=body.encode('utf-8') if body is not None else None, headers=headers or {}
            )
        except httpx.HTTPError as error:
            raise CalDavError(f'{method} {url}: {type(error).__name__}') from error
        if response.status_code >= 400:
            raise CalDavError(f'{method} {url}: HTTP {response.status_code}', response.status_code)
        return response

    def propfind(self, url: str, body: str, depth: str = '0') -> ElementTree.Element:
        response = self._request(
            'PROPFIND', url, body=body, headers={'Depth': depth, 'Content-Type': 'application/xml; charset=utf-8'}
        )
        return _parse_xml(response.text)

    def report(self, url: str, body: str, depth: str = '1') -> ElementTree.Element:
        response = self._request(
            'REPORT', url, body=body, headers={'Depth': depth, 'Content-Type': 'application/xml; charset=utf-8'}
        )
        return _parse_xml(response.text)

    # ---- discovery -------------------------------------------------------------------
    def principal_url(self) -> str:
        root = self.propfind(
            self.base_url,
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>',
        )
        href = root.find('.//d:current-user-principal/d:href', NS)
        text = (href.text or '').strip() if href is not None else ''
        if not text:
            # Radicale answers the principal on the user's own collection root.
            return self.base_url
        return _absolute(self.base_url, text)

    def calendar_home(self) -> str:
        principal = self.principal_url()
        root = self.propfind(
            principal,
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><c:calendar-home-set/></d:prop></d:propfind>',
        )
        href = root.find('.//c:calendar-home-set/d:href', NS)
        text = (href.text or '').strip() if href is not None else ''
        if not text:
            return principal
        return _absolute(self.base_url, text)

    def list_calendars(self, home: Optional[str] = None) -> List[Calendar]:
        home = home or self.calendar_home()
        root = self.propfind(
            home,
            '<?xml version="1.0" encoding="utf-8"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><d:resourcetype/><d:displayname/><c:supported-calendar-component-set/></d:prop>'
            '</d:propfind>',
            depth='1',
        )
        calendars: List[Calendar] = []
        for response in root.findall('d:response', NS):
            href = response.find('d:href', NS)
            if href is None or response.find('.//d:resourcetype/c:calendar', NS) is None:
                continue
            name_el = response.find('.//d:displayname', NS)
            components = [
                (comp.get('name') or '').upper()
                for comp in response.findall('.//c:supported-calendar-component-set/c:comp', NS)
                if comp.get('name')
            ]
            calendars.append(
                Calendar(
                    href=_absolute(self.base_url, (href.text or '').strip()),
                    name=(name_el.text or '').strip() if name_el is not None else '',
                    components=components,
                )
            )
        return calendars

    def make_calendar(self, href: str, name: str, components: Sequence[str]) -> None:
        comps = ''.join(f'<c:comp name="{c}"/>' for c in components)
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:set><d:prop>'
            f'<d:displayname>{_xml_text(name)}</d:displayname>'
            f'<c:supported-calendar-component-set>{comps}</c:supported-calendar-component-set>'
            '</d:prop></d:set></c:mkcalendar>'
        )
        self._request('MKCALENDAR', href, body=body, headers={'Content-Type': 'application/xml; charset=utf-8'})

    # ---- objects ---------------------------------------------------------------------
    def put(self, href: str, ics: str) -> Optional[str]:
        response = self._request('PUT', href, body=ics, headers={'Content-Type': 'text/calendar; charset=utf-8'})
        return response.headers.get('ETag')

    def delete(self, href: str) -> None:
        try:
            self._request('DELETE', href)
        except CalDavError as error:
            if error.status != 404:
                raise

    def query(
        self, calendar_href: str, component: str, *, start: Optional[str] = None, end: Optional[str] = None
    ) -> List[CalendarObject]:
        """calendar-query REPORT for one component kind, optionally within [start, end) (UTC stamps)."""
        time_range = f'<c:time-range start="{start}" end="{end}"/>' if start and end else ''
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><d:getetag/><c:calendar-data/></d:prop>'
            f'<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="{component}">{time_range}'
            '</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>'
        )
        root = self.report(calendar_href, body)
        objects: List[CalendarObject] = []
        for response in root.findall('d:response', NS):
            href = response.find('d:href', NS)
            data = response.find('.//c:calendar-data', NS)
            etag = response.find('.//d:getetag', NS)
            if href is None or data is None or not (data.text or '').strip():
                continue
            objects.append(
                CalendarObject(
                    href=_absolute(self.base_url, (href.text or '').strip()),
                    etag=(etag.text or '').strip() if etag is not None else '',
                    data=data.text or '',
                )
            )
        return objects


def object_href(calendar_href: str, uid: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9._@-]+', '-', uid)
    return urljoin(calendar_href if calendar_href.endswith('/') else calendar_href + '/', f'{safe}.ics')


def _parse_xml(text: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise CalDavError(f'malformed XML from server: {error}') from error


def _xml_text(value: str) -> str:
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
