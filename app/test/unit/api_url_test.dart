import 'package:flutter_test/flutter_test.dart';

import 'package:omi/utils/other/api_url.dart';

void main() {
  test('exactly one slash between base and path', () {
    expect(joinApiUrl('http://192.168.20.5:8000/', 'v1/device-calendar/sync'),
        'http://192.168.20.5:8000/v1/device-calendar/sync');
    expect(joinApiUrl('http://192.168.20.5:8000/', '/dashboard/models'), 'http://192.168.20.5:8000/dashboard/models');
    expect(joinApiUrl('https://omi.example.com', 'v1/x'), 'https://omi.example.com/v1/x');
    expect(joinApiUrl('http://192.168.20.5:8000/', ''), 'http://192.168.20.5:8000/');
  });
}
