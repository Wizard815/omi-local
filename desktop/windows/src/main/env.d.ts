/// <reference types="vite/client" />

// omi-local bakes no service config into the build: the server URL and session
// are runtime state (src/main/omiLocal), and there is no Firebase project or
// crash-report DSN.
declare namespace NodeJS {
  interface ProcessEnv {
    /** '1' enables bench mode: run the fixed workload after load, then quit. */
    OMI_BENCH?: string
    /** '1' enables animation bench: record startup-animation jank, then quit
     *  (skips the DB/IPC workload so it can't pollute frame timing). */
    OMI_ANIM_BENCH?: string
    /** Absolute path to the perf JSONL log. When unset, perf marks are no-ops. */
    OMI_PERF_LOG?: string
    /** Override the SQLite file path (used to point at the throwaway bench DB). */
    OMI_DB_PATH?: string
    /** The desktop-automation bridge (snapshot → plan → approve → execute real
     *  Windows UI actions) is ON by default. Set OMI_AUTOMATION='0' to disable it
     *  (kill-switch for builds that don't want the experimental feature). */
    OMI_AUTOMATION?: string
    /** '1' forces the auto-updater to run against dev-app-update.yml even in an
     *  unpackaged build (local update testing only). */
    OMI_UPDATER_DEV?: string
    /** Pins the omi-local server for this install (see omiLocal/serverSession.ts). */
    OMI_SERVER_URL?: string
    /** A self-hosted electron-builder "generic" update feed; unset = no update checks. */
    OMI_UPDATE_FEED_URL?: string
  }
}
