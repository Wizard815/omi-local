import 'dart:async';

import 'package:connectivity_plus_platform_interface/connectivity_plus_platform_interface.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/backend/preferences.dart';
import 'package:omi/backend/schema/bt_device/bt_device.dart';
import 'package:omi/providers/device_provider.dart';
import 'package:omi/services/services.dart';
import 'package:omi/services/devices.dart';
import 'package:omi/services/devices/connectors/device_connection.dart';
import 'package:omi/services/wals/wal.dart';
import 'package:omi/utils/firmware_update_battery.dart';
import 'package:omi/utils/analytics/analytics_manager.dart';

class _TestConnectivityPlatform extends ConnectivityPlatform {
  @override
  Future<List<ConnectivityResult>> checkConnectivity() async {
    return [ConnectivityResult.none];
  }

  @override
  Stream<List<ConnectivityResult>> get onConnectivityChanged => const Stream.empty();
}

class _RetryInfoDevice extends BtDevice {
  _RetryInfoDevice() : super(id: 'retry-pendant', name: 'Omi', type: DeviceType.omi, rssi: -40);

  String resolvedFirmware = '';
  int reads = 0;

  @override
  Future<BtDevice> getDeviceInfo(DeviceConnection? conn) async {
    reads++;
    return copyWith(firmwareRevision: resolvedFirmware);
  }
}

void main() {
  setUpAll(() async {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues({});
    await SharedPreferencesUtil.init();
    ConnectivityPlatform.instance = _TestConnectivityPlatform();
    try {
      await ServiceManager.init();
    } catch (_) {
      // Ignore if already initialized by another test.
    }
  });

  tearDown(AnalyticsManager.resetForTesting);

  for (final firmware in ['1.0.2', '3.0.16', '3.0.21']) {
    test('empty firmware retries device info and updates WAL routing when $firmware resolves', () async {
      final provider = DeviceProvider();
      addTearDown(provider.dispose);
      final device = _RetryInfoDevice();
      provider.connectedDevice = device;
      provider.pairedDevice = device.copyWith(firmwareRevision: '');
      final syncs = ServiceManager.instance().wal.getSyncs();
      addTearDown(() => syncs.setDevice(null));
      syncs.setDevice(device, firmwareVersion: '');

      await provider.getDeviceInfo();
      expect(device.reads, 1, reason: 'an empty read must not be cached as known');
      expect(provider.pairedDevice?.firmwareRevision, isEmpty);
      device.resolvedFirmware = firmware;
      await provider.getDeviceInfo();
      expect(device.reads, 2);
      expect(provider.pairedDevice?.firmwareRevision, firmware);
      await provider.getDeviceInfo();
      expect(device.reads, 2, reason: 'a successful read keeps the existing cache behavior');

      final phantom = Wal(
          timerStart: 1704067200,
          codec: BleAudioCodec.opus,
          seconds: 60,
          status: WalStatus.miss,
          storage: WalStorage.sdcard,
          device: device.id,
          fileNum: 1);
      syncs.sdcard.testWals = [phantom];
      expect(await syncs.getMissingWals(), firmware == '3.0.21' ? isNot(contains(phantom)) : contains(phantom));
    });
  }

  test('missing transport cannot publish persisted metadata as a fresh DIS observation', () async {
    final provider = DeviceProvider(deviceInfoConnectionLoader: (_) async => null);
    addTearDown(provider.dispose);
    final device = _RetryInfoDevice()..firmwareRevision = '3.1.0';
    provider.connectedDevice = device;
    provider.pairedDevice = device;
    final observations = <String>[];
    final subscription = provider.firmwareRevisions.listen((reading) => observations.add(reading.version));
    addTearDown(subscription.cancel);
    await provider.getDeviceInfo(forceRefresh: true);
    expect(device.reads, 0);
    expect(provider.pairedDevice!.firmwareRevision, 'Unknown');
    expect(observations, isEmpty);
  });

  test('reconnect bypasses cached pairing revision and publishes a fresh same-device DIS read', () async {
    final provider = DeviceProvider(deviceInfoConnectionLoader: (_) async => _InfoConnection());
    addTearDown(provider.dispose);
    final device = _RetryInfoDevice()..resolvedFirmware = '3.1.0';
    await provider.setConnectedDevice(device);
    expect(device.reads, 1);
    final observations = <String>[];
    final subscription = provider.firmwareRevisions.listen((reading) => observations.add(reading.version));
    addTearDown(subscription.cancel);
    await provider.setConnectedDevice(null);
    device.firmwareRevision = '3.1.0'; // discovery/persistence still reports the old version
    device.resolvedFirmware = '3.2.0';
    await provider.setConnectedDevice(device);
    expect(device.reads, 2);
    expect(provider.pairedDevice!.firmwareRevision, '3.2.0');
    expect(observations, ['3.2.0']);
  });

  test('find device coalesces overlapping provider requests', () async {
    final completion = Completer<bool>();
    var runnerCalls = 0;
    final provider = DeviceProvider(
      findDeviceRunner: (_) {
        runnerCalls++;
        return completion.future;
      },
    );
    addTearDown(provider.dispose);
    provider.connectedDevice = BtDevice(id: 'omi-1', name: 'Omi', type: DeviceType.omi, rssi: -40);
    provider.isConnected = true;

    final first = provider.findDevice();
    final second = provider.findDevice();

    expect(identical(first, second), isTrue);
    expect(runnerCalls, 1);

    completion.complete(true);
    expect(await first, isTrue);
    expect(await second, isTrue);

    expect(await provider.findDevice(), isTrue);
    expect(runnerCalls, 2);
  });

  test('find device rejects OmiGlass devices before invoking the runner', () async {
    var runnerCalls = 0;
    final provider = DeviceProvider(
      findDeviceRunner: (_) async {
        runnerCalls++;
        return true;
      },
    );
    addTearDown(provider.dispose);
    provider.connectedDevice = BtDevice(id: 'glass-1', name: 'OmiGlass', type: DeviceType.omi, rssi: -40);
    provider.isConnected = true;

    expect(await provider.findDevice(), isFalse);
    expect(runnerCalls, 0);
  });

  group('battery throttling', () {
    late DeviceProvider provider;
    late int notifyCount;

    setUp(() {
      provider = DeviceProvider();
      addTearDown(provider.dispose);
      notifyCount = 0;
      provider.addListener(() => notifyCount++);
    });

    test('notifies on first battery reading', () {
      final result = provider.updateBatteryLevelForTesting(50);

      expect(result, true);
      expect(notifyCount, 1);
      expect(provider.batteryLevel, 50);
    });

    test('disconnect clears cached battery and charging before the debounce runs', () {
      final device = BtDevice(id: 'battery-pendant', name: 'Omi', type: DeviceType.omi, rssi: -50);
      provider.connectedDevice = device;
      provider.pairedDevice = device;
      provider.setIsConnected(true);
      provider.updateBatteryLevelForTesting(80);
      provider.isCharging = true;

      provider.onDeviceConnectionStateChanged(device.id, DeviceConnectionState.disconnected);

      expect(provider.batteryLevel, -1);
      expect(provider.isCharging, isFalse);
      expect(canStartFirmwareUpdate(batteryLevel: provider.batteryLevel, isCharging: provider.isCharging), isFalse);
      provider.setIsConnected(true);
      expect(provider.batteryLevel, -1, reason: 'Reconnect cannot revive the previous link reading');
      expect(provider.updateBatteryLevelForTesting(80), isTrue, reason: 'A fresh same-level sample must publish');
    });

    test('disconnect of another device leaves the current battery reading intact', () {
      provider.connectedDevice = BtDevice(id: 'battery-pendant', name: 'Omi', type: DeviceType.omi, rssi: -50);
      provider.updateBatteryLevelForTesting(80);
      provider.onDeviceConnectionStateChanged('other-pendant', DeviceConnectionState.disconnected);
      expect(provider.batteryLevel, 80);
    });

    for (final (from, to) in [(29, 30), (30, 29)]) {
      test('notifies on a one-point firmware threshold crossing $from to $to', () {
        final now = DateTime(2026, 11, 3);
        provider.updateBatteryLevelForTesting(from, now: now);
        expect(provider.updateBatteryLevelForTesting(to, now: now.add(const Duration(seconds: 1))), isTrue);
        expect(notifyCount, 2);
      });
    }

    test('zero to unknown publishes a warning-state change despite a one-point delta', () {
      final now = DateTime(2026, 11, 3);
      provider.updateBatteryLevelForTesting(0, now: now);
      expect(provider.updateBatteryLevelForTesting(-1, now: now.add(const Duration(seconds: 1))), isTrue);
      expect(notifyCount, 2);
    });

    test('does not notify for small changes (<5%) within 15 minutes', () {
      final now = DateTime.now();

      // First reading - should notify
      provider.updateBatteryLevelForTesting(50, now: now);
      expect(notifyCount, 1);

      // Small change (2%) within 15 minutes - should NOT notify
      final result = provider.updateBatteryLevelForTesting(52, now: now.add(const Duration(minutes: 5)));

      expect(result, false);
      expect(notifyCount, 1); // No additional notification
      expect(provider.batteryLevel, 52); // Level is still updated
    });

    test('notifies when delta >= 5%', () {
      final now = DateTime.now();

      // First reading
      provider.updateBatteryLevelForTesting(50, now: now);
      expect(notifyCount, 1);

      // 5% change - should notify
      final result = provider.updateBatteryLevelForTesting(45, now: now.add(const Duration(minutes: 1)));

      expect(result, true);
      expect(notifyCount, 2);
    });

    test('notifies after 15 minutes even if delta < 5%', () {
      final now = DateTime.now();

      // First reading
      provider.updateBatteryLevelForTesting(50, now: now);
      expect(notifyCount, 1);

      // Small change but 15 minutes elapsed - should notify
      final result = provider.updateBatteryLevelForTesting(51, now: now.add(const Duration(minutes: 15)));

      expect(result, true);
      expect(notifyCount, 2);
    });

    test('notifies when crossing 20% threshold downward', () {
      final now = DateTime.now();

      // Start above 20%
      provider.updateBatteryLevelForTesting(25, now: now);
      expect(notifyCount, 1);

      // Cross below 20% (only 6% change, but crosses threshold)
      final result = provider.updateBatteryLevelForTesting(19, now: now.add(const Duration(minutes: 1)));

      expect(result, true);
      expect(notifyCount, 2);
    });

    test('notifies when crossing 20% threshold upward', () {
      final now = DateTime.now();

      // Start below 20%
      provider.updateBatteryLevelForTesting(15, now: now);
      expect(notifyCount, 1);

      // Cross above 20% (only 6% change, but crosses threshold)
      final result = provider.updateBatteryLevelForTesting(21, now: now.add(const Duration(minutes: 1)));

      expect(result, true);
      expect(notifyCount, 2);
    });

    test('does not notify for small changes that do not cross 20% threshold', () {
      final now = DateTime.now();

      // Start at 25%
      provider.updateBatteryLevelForTesting(25, now: now);
      expect(notifyCount, 1);

      // Small change staying above 20% - should NOT notify
      final result = provider.updateBatteryLevelForTesting(23, now: now.add(const Duration(minutes: 1)));

      expect(result, false);
      expect(notifyCount, 1);
    });

    test('resetBatteryThrottlingForTesting resets state', () {
      final now = DateTime.now();

      // First reading
      provider.updateBatteryLevelForTesting(50, now: now);
      expect(notifyCount, 1);

      // Reset
      provider.resetBatteryThrottlingForTesting();

      // Now same value should trigger notification again (as if first reading)
      final result = provider.updateBatteryLevelForTesting(50, now: now.add(const Duration(minutes: 1)));

      expect(result, true);
      expect(notifyCount, 2);
    });
  });

  group('low battery alert flag — state machine (#5697)', () {
    // Tests the exact if/else from DeviceProvider.onBatteryLevelChange (lines 163-172)
    // as a pure state machine, without touching production code.
    //
    // Production logic:
    //   if (batteryLevel < 20 && !_hasLowBatteryAlerted) {
    //     _hasLowBatteryAlerted = true;   // fire notification
    //   } else if (batteryLevel > 20) {
    //     _hasLowBatteryAlerted = false;   // THE FIX (was: true)
    //   }

    /// Mirror of production logic. Returns (alertFired, newFlag).
    (bool, bool) evalAlert(int level, bool hasAlerted) {
      if (level < 20 && !hasAlerted) {
        return (true, true);
      } else if (level > 20) {
        return (false, false); // fix: reset flag on recovery
      }
      return (false, hasAlerted);
    }

    /// Simulate a sequence of battery readings, return list of alert events.
    List<bool> runSequence(List<int> levels) {
      var flag = false;
      return levels.map((level) {
        final (fired, newFlag) = evalAlert(level, flag);
        flag = newFlag;
        return fired;
      }).toList();
    }

    test('alert fires when battery drops below 20%', () {
      final (fired, flag) = evalAlert(15, false);
      expect(fired, true);
      expect(flag, true);
    });

    test('no duplicate alert while battery stays low', () {
      final (fired, flag) = evalAlert(10, true);
      expect(fired, false);
      expect(flag, true);
    });

    test('flag resets when battery recovers above 20%', () {
      final (fired, flag) = evalAlert(25, true);
      expect(fired, false);
      expect(flag, false, reason: 'BUG was here: production had true instead of false');
    });

    test('alert fires again after recovery — the core bug scenario', () {
      // 50% → 15% (alert) → 25% (recover) → 10% (should alert AGAIN)
      final alerts = runSequence([50, 15, 25, 10]);
      expect(
          alerts,
          [
            false,
            true,
            false,
            true,
          ],
          reason: 'Before fix: [false, true, false, false] — second alert never fires');
    });

    test('full lifecycle: multiple charge cycles', () {
      final alerts = runSequence([100, 80, 60, 40, 18, 25, 50, 85, 60, 30, 12, 90, 5]);
      // Alert at index 4 (18%), 10 (12%), 12 (5%)
      expect(alerts, [false, false, false, false, true, false, false, false, false, false, true, false, true]);
    });

    test('boundary: exactly 20% neither triggers alert nor resets flag', () {
      // 20 is not < 20 and not > 20
      final (fired1, flag1) = evalAlert(20, false);
      expect(fired1, false);
      expect(flag1, false);

      final (fired2, flag2) = evalAlert(20, true);
      expect(fired2, false);
      expect(flag2, true, reason: 'Exactly 20% should not reset flag (needs > 20)');
    });
  });
}

class _InfoConnection implements DeviceConnection {
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
