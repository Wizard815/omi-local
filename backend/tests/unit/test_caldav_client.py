"""utils/caldav/client.py against a tiny in-memory CalDAV server behind httpx.MockTransport."""

import httpx
import pytest

from utils.caldav.client import CalDavClient, CalDavError, object_href

HOME = '/dav/alice/'


class _Server:
    def __init__(self):
        self.objects = {}  # href path -> ics
        self.calendars = {HOME + 'personal/': ('Personal', ['VEVENT', 'VTODO'])}
        self.requests = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, request.headers.get('Depth')))
        if request.headers.get('Authorization') != 'Basic YWxpY2U6c2VjcmV0':
            return httpx.Response(401)
        path, method = request.url.path, request.method
        body = request.content.decode()
        if method == 'PROPFIND' and 'current-user-principal' in body:
            return httpx.Response(
                207, text=_ms(path, f'<d:current-user-principal><d:href>{HOME}</d:href></d:current-user-principal>')
            )
        if method == 'PROPFIND' and 'calendar-home-set' in body:
            return httpx.Response(
                207, text=_ms(path, f'<c:calendar-home-set><d:href>{HOME}</d:href></c:calendar-home-set>')
            )
        if method == 'PROPFIND':
            responses = [_response(path, '<d:resourcetype><d:collection/></d:resourcetype>')]
            for href, (name, comps) in self.calendars.items():
                comp_xml = ''.join(f'<c:comp name="{c}"/>' for c in comps)
                responses.append(
                    _response(
                        href,
                        '<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>'
                        f'<d:displayname>{name}</d:displayname>'
                        f'<c:supported-calendar-component-set>{comp_xml}</c:supported-calendar-component-set>',
                    )
                )
            return httpx.Response(207, text=_wrap(''.join(responses)))
        if method == 'MKCALENDAR':
            self.calendars[path] = ('made', ['VTODO'])
            return httpx.Response(201)
        if method == 'PUT':
            self.objects[path] = body
            return httpx.Response(201, headers={'ETag': '"e1"'})
        if method == 'DELETE':
            return httpx.Response(204 if self.objects.pop(path, None) else 404)
        if method == 'REPORT':
            want = 'VTODO' if 'name="VTODO"' in body else 'VEVENT'
            responses = ''.join(
                _response(href, f'<d:getetag>"e"</d:getetag><c:calendar-data>{_x(ics)}</c:calendar-data>')
                for href, ics in self.objects.items()
                if href.startswith(path) and f'BEGIN:{want}' in ics
            )
            return httpx.Response(207, text=_wrap(responses))
        return httpx.Response(405)


def _x(text):
    return text.replace('&', '&amp;').replace('<', '&lt;')


def _response(href, props):
    return f'<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'


def _wrap(inner):
    return f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">{inner}</d:multistatus>'


def _ms(href, props):
    return _wrap(_response(href, props))


@pytest.fixture
def server():
    return _Server()


@pytest.fixture
def client(server):
    with CalDavClient('http://dav.test/dav/', 'alice', 'secret', transport=httpx.MockTransport(server.handle)) as c:
        yield c


def test_discovery_lists_calendars_with_their_components(client, server):
    assert client.calendar_home() == 'http://dav.test' + HOME
    (personal,) = client.list_calendars()
    assert personal.name == 'Personal' and personal.href.endswith('/dav/alice/personal/')
    assert personal.supports('VTODO') and personal.components == ['VEVENT', 'VTODO']
    assert server.requests[0] == ('PROPFIND', '/dav/', '0') and server.requests[-1][2] == '1'


def test_put_query_and_delete_round_trip(client, server):
    href = object_href('http://dav.test' + HOME + 'personal/', 'omi-task-1@omi-local')
    assert href.endswith('/personal/omi-task-1@omi-local.ics')
    assert (
        client.put(href, 'BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:omi-task-1@omi-local\r\nEND:VTODO\r\nEND:VCALENDAR\r\n')
        == '"e1"'
    )
    (obj,) = client.query('http://dav.test' + HOME + 'personal/', 'VTODO')
    assert obj.href == href and 'UID:omi-task-1' in obj.data and obj.etag == '"e"'
    assert (
        client.query('http://dav.test' + HOME + 'personal/', 'VEVENT', start='20260101T000000Z', end='20270101T000000Z')
        == []
    )
    client.delete(href)
    client.delete(href)  # a second delete of a gone object is not an error
    assert server.objects == {}


def test_make_calendar_and_auth_failure(client, server):
    client.make_calendar('http://dav.test' + HOME + 'omi-tasks/', 'Omi Tasks', ['VTODO'])
    assert HOME + 'omi-tasks/' in server.calendars
    with CalDavClient('http://dav.test/dav/', 'alice', 'wrong', transport=httpx.MockTransport(server.handle)) as bad:
        with pytest.raises(CalDavError) as error:
            bad.list_calendars()
        assert error.value.status == 401
