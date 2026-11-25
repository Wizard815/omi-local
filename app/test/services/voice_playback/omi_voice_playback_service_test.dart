import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:omi/backend/http/api/tts.dart';
import 'package:omi/backend/preferences.dart';
import 'package:omi/services/voice_playback/omi_voice_playback_service.dart';
import 'package:omi/services/voice_playback/progressive_tts_audio_source.dart';
import 'package:omi/utils/analytics/analytics_manager.dart';
import 'package:omi/utils/analytics/registry/events.g.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../spine/c7_registry_test.dart' show RecordingAdapter;

const _firstSentence = 'This is the first sentence for playback testing.';

/// Two synthesis chunks. The chunker keeps the last terminator inside the first
/// ideal window, so the only period before that window's end is [_firstSentence].
const _twoChunkReply =
    '$_firstSentence And then the reply continues with additional words until the chunker is past the first ideal window.';
final _mp3 = Uint8List.fromList(const [1, 2, 3]);

TtsSynthesisRequest _streamRequest(Stream<List<int>> bytes, {int? contentLength, Future<void> Function()? onCancel}) {
  Future<void> cancel() => onCancel?.call() ?? Future<void>.value();

  return TtsSynthesisRequest(
    response: Future.value(
      TtsAudioStream(bytes: bytes, contentLength: contentLength, contentType: 'audio/mpeg', cancel: cancel),
    ),
    cancel: cancel,
  );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late OmiVoicePlaybackService service;
  late RecordingAdapter adapter;
  late List<Completer<void>> delays;
  late List<Uint8List> plays;
  late List<String> spoken;
  var now = DateTime.utc(2026, 9, 26);
  var probed = false;

  setUp(() async {
    AnalyticsManager.resetForTesting();
    SharedPreferences.setMockInitialValues({});
    PackageInfo.setMockInitialValues(
      appName: 'Omi Test',
      packageName: 'com.omi.test',
      version: '1.0.543',
      buildNumber: '992',
      buildSignature: '',
    );
    await SharedPreferencesUtil.init();
    service = OmiVoicePlaybackService.instance;
    service.debugReset();
    adapter = RecordingAdapter();
    AnalyticsManager.configure(adapter);
    await AnalyticsManager.init();
    delays = [];
    plays = [];
    spoken = [];
    now = DateTime.utc(2026, 9, 26);
    probed = false;
  });

  tearDown(() {
    service.debugReset();
    AnalyticsManager.resetForTesting();
  });

  Future<void> install({
    Future<Uint8List?> Function(String text, {String? voiceId})? synthesize,
    TtsSynthesisRequest Function(String text, {String? voiceId})? synthesizeStream,
    Future<void> Function(Uint8List bytes)? play,
    Future<Duration> Function(ProgressiveTtsAudioSource source)? playStream,
    Future<void> Function()? stopPlayback,
    Duration? streamStallTimeout,
    VoicePlaybackOutputSnapshot output = const VoicePlaybackOutputSnapshot(
      headphonesConnected: true,
      checkFailed: false,
      route: VoiceReplyPlaybackOutputRoute.bluetooth,
    ),
  }) async {
    assert(synthesize != null || synthesizeStream != null);
    service.debugHooks = VoicePlaybackDebugHooks(
      synthesize:
          synthesize == null ? null : ({required String text, String? voiceId}) => synthesize(text, voiceId: voiceId),
      synthesizeStream: synthesizeStream == null
          ? null
          : ({required String text, String? voiceId}) => synthesizeStream(text, voiceId: voiceId),
      play: play ?? (bytes) async => plays.add(bytes),
      playStream: playStream,
      stopPlayback: stopPlayback ?? () async {},
      speak: (text) async => spoken.add(text),
      stopSpeak: () async {},
      probeOutput: () async {
        probed = true;
        return output;
      },
      now: () => now,
      delay: (duration) {
        expect(duration, const Duration(milliseconds: 500));
        final completer = Completer<void>();
        delays.add(completer);
        return completer.future;
      },
      streamStallTimeout: streamStallTimeout,
    );
  }

  Future<void> releaseDelays() async {
    for (var i = 0; i < 5 && delays.isNotEmpty; i++) {
      final pending = List<Completer<void>>.of(delays);
      delays.clear();
      for (final completer in pending) {
        if (!completer.isCompleted) completer.complete();
      }
      await pumpEventQueue();
    }
  }

  Future<void> flush() async {
    await AnalyticsManager.flushPending(force: true);
    await AnalyticsManager.flushPending(force: true);
  }

  Future<void> finishPlayback() async {
    await pumpEventQueue();
    service.debugNotifyPlaybackCompleted();
    await pumpEventQueue();
    await releaseDelays();
    await flush();
  }

  test('retired cleanup cannot stop a replacement response after a native stop settles', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    final stopped = Completer<void>();
    var holdStop = false;
    var fallbackStops = 0;
    service.debugHooks = VoicePlaybackDebugHooks(
      probeOutput: () async => const VoicePlaybackOutputSnapshot(
          headphonesConnected: true, checkFailed: false, route: VoiceReplyPlaybackOutputRoute.bluetooth),
      stopPlayback: () => holdStop ? stopped.future : Future.value(),
      stopSpeak: () async => fallbackStops++,
    );
    await service.beginResponse(messageId: 'old');
    holdStop = true;
    final cleanup = service.interruptResponse(messageId: 'old', source: VoiceReplyPlaybackInterruptSource.streamError);
    await pumpEventQueue();
    holdStop = false;
    await service.beginResponse(messageId: 'replacement');
    stopped.complete();
    await cleanup;
    expect(fallbackStops, 0, reason: 'old cleanup cannot stop fallback belonging to a newer response');
  });

  for (final skip in ['mode off', 'no headphones', 'revoked admission']) {
    test('skipped $skip attempt must not abandon existing response cleanup', () async {
      SharedPreferencesUtil().voiceResponseMode = 2;
      final stopped = Completer<void>();
      var holdStop = false;
      var headphones = true;
      var fallbackStops = 0;
      service.debugHooks = VoicePlaybackDebugHooks(
        probeOutput: () async => VoicePlaybackOutputSnapshot(
            headphonesConnected: headphones, checkFailed: false, route: VoiceReplyPlaybackOutputRoute.bluetooth),
        stopPlayback: () => holdStop ? stopped.future : Future<void>.value(),
        stopSpeak: () async => fallbackStops++,
      );
      await service.beginResponse(messageId: 'old');
      holdStop = true;
      final cleanup =
          service.interruptResponse(messageId: 'old', source: VoiceReplyPlaybackInterruptSource.streamError);
      await pumpEventQueue();
      holdStop = false;
      if (skip == 'mode off') SharedPreferencesUtil().voiceResponseMode = 0;
      if (skip == 'no headphones') {
        SharedPreferencesUtil().voiceResponseMode = 1;
        headphones = false;
      }
      await service.beginResponse(messageId: 'not-admitted', canBegin: () => skip != 'revoked admission');
      stopped.complete();
      await cleanup;
      expect(fallbackStops, 1,
          reason: 'A skipped attempt owns no playback; existing cleanup must still stop fallback and release audio');
    });
  }

  for (final skip in ['mode off', 'no headphones', 'revoked admission']) {
    test('skipped $skip attempt must let an admitted begin finish clearing prior audio', () async {
      SharedPreferencesUtil().voiceResponseMode = 2;
      final cancelEntered = Completer<void>();
      final cancelDone = Completer<void>();
      final response = Completer<TtsAudioStream?>();
      var headphones = true;
      var playerStops = 0;
      service.debugHooks = VoicePlaybackDebugHooks(
        probeOutput: () async => VoicePlaybackOutputSnapshot(
            headphonesConnected: headphones, checkFailed: false, route: VoiceReplyPlaybackOutputRoute.bluetooth),
        stopPlayback: () async => playerStops++,
        synthesizeStream: ({required text, voiceId}) => TtsSynthesisRequest(
          response: response.future,
          cancel: () {
            if (!cancelEntered.isCompleted) cancelEntered.complete();
            return cancelDone.future;
          },
        ),
      );
      await service.beginResponse(messageId: 'old');
      service.updateStreamingResponse(messageId: 'old', fullText: _firstSentence, isFinal: true);
      await pumpEventQueue();
      final preparing = service.beginResponse(messageId: 'preparing-replacement');
      await cancelEntered.future;
      if (skip == 'mode off') SharedPreferencesUtil().voiceResponseMode = 0;
      if (skip == 'no headphones') {
        SharedPreferencesUtil().voiceResponseMode = 1;
        headphones = false;
      }
      await service.beginResponse(messageId: 'not-admitted', canBegin: () => skip != 'revoked admission');
      cancelDone.complete();
      await preparing;
      try {
        expect(playerStops, 2, reason: 'A skipped begin cannot abandon prior audio cleanup already in progress');
      } finally {
        response.complete(null);
        await pumpEventQueue();
      }
    });
  }

  test('mode off emits skipped once and does not probe output', () async {
    SharedPreferencesUtil().voiceResponseMode = 0;
    await service.beginResponse(messageId: 'off');
    await flush();

    expect(probed, isFalse);

    await service.interrupt(source: VoiceReplyPlaybackInterruptSource.userTyped);
    await flush();
  });

  test('preview plays without telemetry and does not interrupt a real reply lifecycle', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    await install(synthesize: (_, {voiceId}) async => _mp3);

    await service.playPreview('A settings preview answer.');
    await flush();
    expect(plays, [_mp3]);

    await service.beginResponse(messageId: 'real-reply');
    await service.playPreview('A second settings preview answer.');
    await flush();
    expect(plays, [_mp3]);

    await service.interrupt(source: VoiceReplyPlaybackInterruptSource.userTyped);
    await flush();
  });

  test('no headphones emits skipped and does not synthesize', () async {
    SharedPreferencesUtil().voiceResponseMode = 1;
    var synthesized = false;
    await install(
      output: const VoicePlaybackOutputSnapshot(
        headphonesConnected: false,
        checkFailed: false,
        route: VoiceReplyPlaybackOutputRoute.speaker,
      ),
      synthesize: (text, {voiceId}) async {
        synthesized = true;
        return _mp3;
      },
    );

    await service.beginResponse(messageId: 'quiet');
    await flush();

    expect(probed, isTrue);
    expect(synthesized, isFalse);
    expect(plays, isEmpty);
  });

  test('headphone check failure is its own skip reason', () async {
    SharedPreferencesUtil().voiceResponseMode = 1;
    await install(
      output: const VoicePlaybackOutputSnapshot(
        headphonesConnected: false,
        checkFailed: true,
        route: VoiceReplyPlaybackOutputRoute.unknown,
      ),
      synthesize: (_, {voiceId}) async => _mp3,
    );

    await service.beginResponse(messageId: 'probe-failed');
    await flush();
  });

  test('played reports chunk counts, route, and latency to first play', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    await install(synthesize: (_, {voiceId}) async => _mp3);

    await service.beginResponse(messageId: 'played');
    now = now.add(const Duration(milliseconds: 320));
    service.updateStreamingResponse(messageId: 'played', fullText: _firstSentence, isFinal: true);
    await finishPlayback();

    expect(plays, [_mp3]);
    expect(spoken, isEmpty);
  });

  test('a synthesis error drops that chunk and still plays the next', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    var calls = 0;
    await install(
      synthesize: (text, {voiceId}) async {
        calls++;
        if (calls == 1) throw Exception('synth failed');
        return _mp3;
      },
    );

    await service.beginResponse(messageId: 'drop');
    service.updateStreamingResponse(messageId: 'drop', fullText: _twoChunkReply, isFinal: true);
    await finishPlayback();

    expect(calls, 2);
    expect(plays, hasLength(1));
  });

  test('429 falls back to on-device speech and emits fallback_only', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    await install(synthesize: (_, {voiceId}) async => throw TtsUnavailableException(429));

    await service.beginResponse(messageId: 'limited');
    now = now.add(const Duration(milliseconds: 80));
    service.updateStreamingResponse(messageId: 'limited', fullText: _firstSentence, isFinal: true);
    await pumpEventQueue();
    await releaseDelays();
    await flush();

    expect(plays, isEmpty);
    expect(spoken, [_firstSentence]);
  });

  test('interrupt mid-play records the caller source', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    await install(synthesize: (_, {voiceId}) async => _mp3);

    await service.beginResponse(messageId: 'cut');
    service.updateStreamingResponse(messageId: 'cut', fullText: _firstSentence, isFinal: true);
    await pumpEventQueue();
    expect(plays, hasLength(1));

    await service.interrupt(source: VoiceReplyPlaybackInterruptSource.userTyped);
    await flush();
  });

  test('a lifecycle emits exactly once across finish, interrupt, and a rapid double-call', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    await install(synthesize: (_, {voiceId}) async => _mp3);

    await service.beginResponse(messageId: 'once');
    service.updateStreamingResponse(messageId: 'once', fullText: _firstSentence, isFinal: true);
    await pumpEventQueue();
    expect(service.isSpeaking, isTrue);
    await service.beginResponse(messageId: 'once');
    await finishPlayback();

    await service.interrupt(source: VoiceReplyPlaybackInterruptSource.newVoiceQuery);
    service.debugNotifyPlaybackCompleted();
    await releaseDelays();
    await flush();

    await service.beginResponse(messageId: 'again');
    service.updateStreamingResponse(messageId: 'again', fullText: _firstSentence, isFinal: true);
    await pumpEventQueue();
    await service.beginResponse(messageId: 'again');
    service.debugNotifyPlaybackCompleted();
    service.debugNotifyPlaybackCompleted();
    await pumpEventQueue();
    await releaseDelays();
    await flush();
  });

  test('idle before isFinal stays open and counts the later chunk', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    await install(synthesize: (_, {voiceId}) async => _mp3);

    await service.beginResponse(messageId: 'slow');
    service.updateStreamingResponse(messageId: 'slow', fullText: _firstSentence, isFinal: false);
    await pumpEventQueue();
    service.debugNotifyPlaybackCompleted();
    await pumpEventQueue();
    await releaseDelays();
    await flush();

    expect(plays, hasLength(1));

    service.updateStreamingResponse(messageId: 'slow', fullText: _twoChunkReply, isFinal: true);
    await pumpEventQueue();
    service.debugNotifyPlaybackCompleted();
    await pumpEventQueue();
    await releaseDelays();
    await flush();

    expect(plays, hasLength(2));
  });

  test('interrupt during the first chunk keeps first-audio latency', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    final playing = Completer<void>();
    await install(
      synthesize: (_, {voiceId}) async => _mp3,
      play: (bytes) {
        plays.add(bytes);
        return playing.future;
      },
    );

    await service.beginResponse(messageId: 'mid-chunk');
    now = now.add(const Duration(milliseconds: 40));
    service.updateStreamingResponse(messageId: 'mid-chunk', fullText: _firstSentence, isFinal: true);
    await pumpEventQueue();
    expect(plays, hasLength(1));

    await service.interrupt(source: VoiceReplyPlaybackInterruptSource.userTyped);
    if (!playing.isCompleted) playing.complete();
    await pumpEventQueue();
    await flush();
  });

  test('progressive playback starts after prebuffer before the response completes', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    final bytes = StreamController<List<int>>();
    final playbackStarted = Completer<void>();
    addTearDown(() async {
      if (!bytes.isClosed) await bytes.close();
    });
    await install(
      synthesizeStream: (_, {voiceId}) => _streamRequest(bytes.stream, contentLength: 8192),
      playStream: (source) async {
        playbackStarted.complete();
        await source.settled;
        return const Duration(milliseconds: 300);
      },
    );

    await service.beginResponse(messageId: 'progressive');
    service.updateStreamingResponse(messageId: 'progressive', fullText: _firstSentence, isFinal: true);
    bytes.add(Uint8List(progressiveTtsPrebufferBytes));

    await playbackStarted.future.timeout(const Duration(seconds: 1));
    expect(bytes.isClosed, isFalse, reason: 'playback must begin while HTTP bytes are still arriving');

    bytes.add(Uint8List(progressiveTtsPrebufferBytes));
    await bytes.close();
    await pumpEventQueue();
    await releaseDelays();
    await flush();

    expect(spoken, isEmpty);
  });

  test('mid-stream failure plays buffered audio then speaks only the remaining suffix', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    final bytes = StreamController<List<int>>();
    final playbackStarted = Completer<void>();
    await install(
      synthesizeStream: (_, {voiceId}) => _streamRequest(bytes.stream, contentLength: progressiveTtsPrebufferBytes * 2),
      playStream: (source) async {
        playbackStarted.complete();
        await source.settled;
        return const Duration(milliseconds: 300);
      },
    );

    await service.beginResponse(messageId: 'partial');
    service.updateStreamingResponse(messageId: 'partial', fullText: _firstSentence, isFinal: true);
    bytes.add(Uint8List(progressiveTtsPrebufferBytes));
    await playbackStarted.future.timeout(const Duration(seconds: 1));
    bytes.addError(StateError('connection reset'));
    await bytes.close();
    await pumpEventQueue();
    await releaseDelays();
    await flush();

    expect(spoken, hasLength(1));
    expect(spoken.single, isNot(_firstSentence));
    expect(_firstSentence.endsWith(spoken.single), isTrue);
  });

  test('interrupt cancels the response subscription and abort trigger without fallback', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    var subscriptionCancelled = false;
    var requestCancelled = false;
    final bytes = StreamController<List<int>>(onCancel: () => subscriptionCancelled = true);
    final playbackStarted = Completer<void>();
    await install(
      synthesizeStream: (_, {voiceId}) =>
          _streamRequest(bytes.stream, contentLength: 8192, onCancel: () async => requestCancelled = true),
      playStream: (source) async {
        playbackStarted.complete();
        await source.settled;
        return const Duration(milliseconds: 100);
      },
    );

    await service.beginResponse(messageId: 'cancel-stream');
    service.updateStreamingResponse(messageId: 'cancel-stream', fullText: _firstSentence, isFinal: true);
    bytes.add(Uint8List(progressiveTtsPrebufferBytes));
    await playbackStarted.future.timeout(const Duration(seconds: 1));
    await service.interrupt(source: VoiceReplyPlaybackInterruptSource.userTyped);
    await pumpEventQueue();
    await flush();

    expect(subscriptionCancelled, isTrue);
    expect(requestCancelled, isTrue);
    expect(spoken, isEmpty);
  });

  test('a stream with no bytes times out and falls back instead of hanging', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    final bytes = StreamController<List<int>>();
    var playCalled = false;
    await install(
      synthesizeStream: (_, {voiceId}) => _streamRequest(bytes.stream),
      playStream: (source) async {
        playCalled = true;
        return Duration.zero;
      },
      streamStallTimeout: const Duration(milliseconds: 20),
    );

    await service.beginResponse(messageId: 'stall');
    service.updateStreamingResponse(messageId: 'stall', fullText: _firstSentence, isFinal: true);
    await Future<void>.delayed(const Duration(milliseconds: 60));
    await pumpEventQueue();
    await releaseDelays();
    await flush();

    expect(playCalled, isFalse);
    expect(spoken, [_firstSentence]);
  });

  test('multiple progressive chunks preserve synthesis and playback order', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    final synthesized = <String>[];
    final playedIds = <int>[];
    var nextId = 0;
    await install(
      synthesizeStream: (text, {voiceId}) {
        synthesized.add(text);
        final id = ++nextId;
        return _streamRequest(
          Stream<List<int>>.fromIterable([Uint8List.fromList(List<int>.filled(progressiveTtsPrebufferBytes, id))]),
          contentLength: progressiveTtsPrebufferBytes,
        );
      },
      playStream: (source) async {
        final data = await source.collectBytes();
        playedIds.add(data.first);
        return const Duration(milliseconds: 100);
      },
    );

    await service.beginResponse(messageId: 'ordered');
    service.updateStreamingResponse(messageId: 'ordered', fullText: _twoChunkReply, isFinal: true);
    await pumpEventQueue();
    await releaseDelays();
    await flush();

    expect(synthesized, [_firstSentence, _twoChunkReply.substring(_firstSentence.length).trim()]);
    expect(playedIds, [1, 2]);
  });

  test('a superseded in-flight synthesis cannot enter the next lifecycle', () async {
    SharedPreferencesUtil().voiceResponseMode = 2;
    final oldSynthesis = Completer<Uint8List?>();
    var synthesisCalls = 0;
    await install(
      synthesize: (_, {voiceId}) {
        synthesisCalls++;
        if (synthesisCalls == 1) return oldSynthesis.future;
        return Future.value(_mp3);
      },
    );

    await service.beginResponse(messageId: 'old');
    service.updateStreamingResponse(messageId: 'old', fullText: _firstSentence, isFinal: true);
    await pumpEventQueue();

    await service.beginResponse(messageId: 'new');
    oldSynthesis.complete(_mp3);
    await pumpEventQueue();
    expect(plays, isEmpty);

    service.updateStreamingResponse(messageId: 'new', fullText: _firstSentence, isFinal: true);
    await finishPlayback();

    expect(plays, [_mp3]);
  });
}
