import {
  Settings as SettingsIcon,
  History,
  ShieldCheck,
  CircleUserRound,
  SlidersHorizontal,
  Brain,
  Bot,
  AudioLines,
  Keyboard,
  Bell,
  Info,
  type LucideIcon
} from 'lucide-react'

export type SettingsTabId =
  | 'general'
  | 'memories'
  | 'agents'
  | 'transcription'
  | 'rewind'
  | 'notifications'
  | 'privacy'
  | 'account'
  | 'plan-usage'
  | 'shortcuts'
  | 'advanced'
  | 'about'

// 'plan-usage' stays a known id (old deep links) but is not listed or rendered:
// omi-local has no plans, billing or cloud quota.
export const SETTINGS_TABS: { id: SettingsTabId; label: string; Icon: LucideIcon }[] = [
  { id: 'general', label: 'General', Icon: SettingsIcon },
  { id: 'memories', label: 'Memories', Icon: Brain },
  { id: 'agents', label: 'Agents', Icon: Bot },
  { id: 'transcription', label: 'Transcription', Icon: AudioLines },
  { id: 'rewind', label: 'Rewind', Icon: History },
  { id: 'notifications', label: 'Notifications', Icon: Bell },
  { id: 'privacy', label: 'Privacy', Icon: ShieldCheck },
  { id: 'account', label: 'Account', Icon: CircleUserRound },
  { id: 'shortcuts', label: 'Shortcuts', Icon: Keyboard },
  { id: 'advanced', label: 'Advanced', Icon: SlidersHorizontal },
  { id: 'about', label: 'About', Icon: Info }
]
