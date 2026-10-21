import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_core_platform_interface/test.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/utils/debugging/crashlytics_manager.dart';

class LocalReport {
  final Object error;
  final StackTrace stack;
  final bool fatal;

  LocalReport(this.error, this.stack, this.fatal);
}

/// Records both what reaches the Crashlytics platform channel ([recordErrors],
/// which must stay empty: this build sends nothing to Google) and what the app
/// reports through CrashlyticsManager's on-device sink ([reports]).
class CrashlyticsRecorder {
  final calls = <MethodCall>[];
  final reports = <LocalReport>[];
  Object? recordErrorFailure;

  List<MethodCall> get recordErrors => calls.where((call) => call.method == 'Crashlytics#recordError').toList();

  void reset() {
    calls.clear();
    reports.clear();
    recordErrorFailure = null;
  }
}

class _CrashlyticsCapableFirebaseCore implements TestFirebaseCoreHostApi {
  @override
  Future<PigeonInitializeResponse> initializeApp(
    String appName,
    PigeonFirebaseOptions initializeAppRequest,
  ) async {
    return PigeonInitializeResponse(
      name: appName,
      options: initializeAppRequest,
      pluginConstants: const {
        'plugins.flutter.io/firebase_crashlytics': {'isCrashlyticsCollectionEnabled': true},
      },
    );
  }

  @override
  Future<List<PigeonInitializeResponse?>> initializeCore() async => [];

  @override
  Future<PigeonFirebaseOptions> optionsFromResource() async => throw UnimplementedError();
}

Future<CrashlyticsRecorder> installCrashlyticsRecorder() async {
  final recorder = CrashlyticsRecorder();
  TestFirebaseCoreHostApi.setup(_CrashlyticsCapableFirebaseCore());
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
    const MethodChannel('plugins.flutter.io/firebase_crashlytics'),
    (call) async {
      recorder.calls.add(call);
      if (call.method == 'Crashlytics#recordError' && recorder.recordErrorFailure != null) {
        throw recorder.recordErrorFailure!;
      }
      return null;
    },
  );
  CrashlyticsManager.localRecord = (error, stack, {required fatal}) async {
    if (recorder.recordErrorFailure != null) throw recorder.recordErrorFailure!;
    recorder.reports.add(LocalReport(error, stack, fatal));
  };
  if (Firebase.apps.isEmpty) {
    await Firebase.initializeApp(
      options: const FirebaseOptions(
        apiKey: 'fake',
        appId: '1:1:ios:fake',
        messagingSenderId: '1',
        projectId: 'demo-omi-local',
      ),
    );
  }
  return recorder;
}
