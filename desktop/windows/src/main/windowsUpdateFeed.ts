export type WindowsUpdateChannel = 'stable' | 'beta'
/**
 * The update feed, if the user hosts one (omi-local has no update server).
 *
 * OMI_UPDATE_FEED_URL names an electron-builder "generic" feed: a directory the
 * user serves over http(s) with the latest.yml (Windows) or latest-linux.yml
 * (AppImage) and the installers `pnpm build:win` / `build:linux` write to dist/.
 * Unset (the default) means the app never checks for updates and contacts no
 * update host at all. The beta channel reads the same directory with prereleases
 * allowed.
 */
export function selfHostedUpdateFeedUrl(raw: string | undefined): string | null {
  const value = raw?.trim()
  if (!value) return null
  let url: URL
  try {
    url = new URL(value)
  } catch {
    return null
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') return null
  if (url.username || url.password || url.search || url.hash) return null
  return url.toString().endsWith('/') ? url.toString() : `${url.toString()}/`
}

type ResolveFeed = (channel: WindowsUpdateChannel) => Promise<string>
type ApplyFeed = (feedUrl: string) => void

/**
 * Resolves an immutable feed before every update check. Concurrent checks share
 * one resolution request, and a response for a channel the user just left can
 * never overwrite the newly selected channel.
 */
export class WindowsUpdateFeedSelector {
  private selectedChannel: WindowsUpdateChannel
  private configuredChannel: WindowsUpdateChannel | null = null
  private configuredUrl: string | null = null
  private readonly inFlight = new Map<WindowsUpdateChannel, Promise<string>>()

  constructor(
    initialChannel: WindowsUpdateChannel,
    private readonly resolveFeed: ResolveFeed,
    private readonly applyFeed: ApplyFeed
  ) {
    this.selectedChannel = initialChannel
  }

  select(channel: WindowsUpdateChannel): void {
    this.selectedChannel = channel
  }

  async prepareSelected(): Promise<void> {
    while (true) {
      const channel = this.selectedChannel
      const feedUrl = await this.resolveOnce(channel)

      if (this.selectedChannel !== channel) {
        if (this.configuredChannel === this.selectedChannel) return
        continue
      }
      if (this.configuredChannel !== channel || this.configuredUrl !== feedUrl) {
        this.applyFeed(feedUrl)
        this.configuredChannel = channel
        this.configuredUrl = feedUrl
      }
      return
    }
  }

  private async resolveOnce(channel: WindowsUpdateChannel): Promise<string> {
    const existing = this.inFlight.get(channel)
    if (existing) return existing

    const request = this.resolveFeed(channel)
    this.inFlight.set(channel, request)
    try {
      return await request
    } finally {
      if (this.inFlight.get(channel) === request) this.inFlight.delete(channel)
    }
  }
}
