import 'package:flutter/material.dart';

import 'package:omi/utils/chat/slash_commands.dart';

/// The list that pops up above the chat input while a "/command" is typed.
class SlashCommandPopup extends StatelessWidget {
  final List<SlashCommandSuggestion> suggestions;
  final ValueChanged<SlashCommandSuggestion> onSelected;

  const SlashCommandPopup({super.key, required this.suggestions, required this.onSelected});

  @override
  Widget build(BuildContext context) {
    if (suggestions.isEmpty) return const SizedBox.shrink();
    return Container(
      margin: const EdgeInsets.fromLTRB(12, 0, 12, 6),
      constraints: const BoxConstraints(maxHeight: 260),
      decoration: BoxDecoration(
        color: const Color(0xFF1F1F25),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0xFF35343B)),
      ),
      child: ListView.separated(
        shrinkWrap: true,
        padding: const EdgeInsets.symmetric(vertical: 4),
        itemCount: suggestions.length,
        separatorBuilder: (_, __) => const Divider(height: 1, color: Color(0xFF2A2A30)),
        itemBuilder: (context, index) {
          final suggestion = suggestions[index];
          return InkWell(
            onTap: () => onSelected(suggestion),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    suggestion.usage,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 14,
                      fontFamily: 'Ubuntu Mono',
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(suggestion.description, style: TextStyle(color: Colors.grey.shade400, fontSize: 12.5)),
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}
