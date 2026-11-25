import 'package:flutter/material.dart';

import 'package:omi/pages/chat/widgets/phone_llm_section.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// Settings → AI on this phone (Android only): the same section the chat's /model sheet shows.
class PhoneLlmSettingsPage extends StatelessWidget {
  const PhoneLlmSettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(leading: const OmiBackButton(), title: Text(context.l10n.phoneLlmTitle)),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(OmiSpacing.lg, OmiSpacing.lg, OmiSpacing.lg, OmiSpacing.xxl),
        children: const [PhoneLlmSection()],
      ),
    );
  }
}
