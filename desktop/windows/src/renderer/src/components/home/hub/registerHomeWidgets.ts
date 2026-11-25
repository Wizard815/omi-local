import { registerHubHomeWidgets } from './hubHomeWidgetsSlot'
import { createElement } from 'react'
import { HomeGoalsChips } from '../HomeGoalsChips'
import { WaitingForOkChip } from '../WaitingForOkChip'
import type { HubHomeWidgetsProps } from './hubHomeWidgetsSlot'

// Register the focused-goals chip row as the resting Hub's home-widget row.
//
// EAGER (unlike connections/register.ts's React.lazy): HomeHub mounts this widget
// directly in the resting cluster, with no Suspense boundary, so a lazy component
// would throw on first render. The module is small and its deps (goal libs, api
// client, firebase) are already in the main bundle, so eager import costs nothing
// extra. The widget only *fetches* when the hub actually mounts it on the main
// window — registration alone does no work.
// omi-local adds the "N waiting for your OK" chip above the goals row.
function HomeRestingWidgets(props: HubHomeWidgetsProps): React.JSX.Element {
  return createElement(
    'div',
    { className: 'flex flex-col items-start' },
    createElement(WaitingForOkChip),
    createElement(HomeGoalsChips, props)
  )
}

registerHubHomeWidgets(HomeRestingWidgets)
