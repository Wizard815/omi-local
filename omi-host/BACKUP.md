# Backups and restore

Everything Omi knows about you lives in one Docker volume, `omi-data`, on the server's
disk. This page sets up a daily copy of it on another disk (normally a share on your NAS),
shows how to test that copy, and how to get everything back after the server's disk dies.

The stack already keeps a few tarballs **on the same disk** (`/data/backups`,
`maintenance.sh`); they help after a bad update, not after a dead disk. Keep them, and add this.

## What is backed up

| In the backup | What it is |
|---|---|
| `firebase-export/` | Conversations, memories, tasks, day summaries, accounts and logins (the Firestore and Auth emulators). Exported fresh just before the backup. |
| `redis/dump.rdb` | Dashboard settings: model picks, chat shortcuts, the model allowlist. Snapshotted (`BGSAVE`) right after the export. |
| `storage/` | Recordings and other audio, speech profiles, chat files. |
| `chroma/` | The vector store (search, task de-duplication). SQLite files are copied with SQLite's own backup method, so a write in progress cannot corrupt the copy. |
| `radicale/collections/` | Calendars and task lists, read while holding Radicale's own lock. |
| `secrets/` | The key that signs logins, the CalDAV password, the key for recording links, the key for memory-list cursors, and on installs made since November 2026 the key stored data is encrypted with (`encryption_secret`). Restoring them keeps phones signed in and DAVx5 syncing, and without `encryption_secret` the restored data cannot be read. |
| `.env` | **Not by default**: it holds your API keys. See [Encryption](#encryption-optional). |

Left out because they download again or are rebuilt: speech and LLM models (`hf-cache`,
`whispercpp`, `piper`), GPU kernel caches (`miopen`), logs, and the same-disk tarballs.

Each backup is two files in `OMI_BACKUP_DIR`:

- `omi-backup-20261125T033000Z.tar.gz` (`.age` or `.gpg` when encrypted): the archive. Inside
  it, `MANIFEST.json` lists every file with its size and SHA-256, the git commit the stack
  ran, the Firestore export and Redis versions, and counts (calendar items, vectors, files).
- `omi-backup-20261125T033000Z.json`: the archive's own SHA-256 and size, when it was made,
  and any warnings. The health page reads this to show the age of the last backup.

## 1. Mount the NAS share on the server

Create a share on the NAS (for example `omi-backups`) that only the server can write to.
Then mount it on the server, the host that runs Docker, at a fixed path. The examples
use `/mnt/nas/omi-backups` and `192.0.2.20` for the NAS; use your own.

**Unraid:** install the *Unassigned Devices* plugin, then *Main → Remote SMB/NFS Shares →
Add Remote SMB/NFS Share*, and switch on *Auto Mount*. Unraid mounts it at
`/mnt/remotes/<name>`; use that path below.

**Any Linux, NFS** (`/etc/fstab`, then `mkdir -p /mnt/nas/omi-backups && mount -a`):

```
192.0.2.20:/volume1/omi-backups  /mnt/nas/omi-backups  nfs  defaults,_netdev,nofail  0 0
```

**Any Linux, SMB** (put the NAS login in `/root/.smb-omi` as `username=...` and
`password=...` lines, `chmod 600` it):

```
//192.0.2.20/omi-backups  /mnt/nas/omi-backups  cifs  credentials=/root/.smb-omi,uid=0,gid=0,_netdev,nofail  0 0
```

Check it: `touch /mnt/nas/omi-backups/x && rm /mnt/nas/omi-backups/x`.

If the share is not mounted, the backup service refuses to start (`run-omi-local-host.sh`
warns) instead of filling the server's own disk.

## 2. Turn backups on

In `omi-host/.env`:

```
OMI_BACKUP_ENABLED=1
OMI_BACKUP_DIR=/mnt/nas/omi-backups
OMI_BACKUP_TIME=03:30          # daily
TZ=Europe/Berlin               # your time zone (default UTC)
```

Then rebuild and start: `bash omi-host/run-omi-local-host.sh`. This starts the
`omi-backup` container. It uses the omi-local image (Java, firebase-tools, redis-server and
`age` are all in it) and needs no Docker socket and no internet.

Take the first backup now instead of waiting for the night:

```
bash omi-host/backup.sh
bash omi-host/restore.sh --dry-run     # checks it, writes nothing
```

`backup.sh` prints the archive's name, size and checksum, and any warnings. A warning
saying the quiesce failed means omi-local runs an image older than this tool. Rebuild it
with `run-omi-local-host.sh`.

### How the backup stays consistent while the stack runs

The Firestore emulator keeps its data in memory. The backup asks omi-local to export it
and to snapshot Redis, waits for the answer, then reads the files:

- The backup writes `.snapshot-request` into the data volume. omi-local's maintenance loop
  (`maintenance.sh`) picks it up within seconds, exports, snapshots Redis and answers in
  `.snapshot-done`. Going through the loop means it never collides with the loop's own
  15-minute export, and the `omi-backup` container needs no Docker socket.
- If nothing picks the request up within 30 seconds (omi-local runs an image older than
  this tool), `backup.sh` on the host falls back to `docker exec omi-local` running the
  same two functions.

Writes that land after the export go into the next backup. Nothing in the stack is stopped.

### Retention

After each backup, older ones are pruned. The defaults keep the newest backup of each of
the last 7 days, 4 weeks and 6 months: at most 17 archives, covering about half a year.

```
OMI_BACKUP_KEEP_DAILY=7
OMI_BACKUP_KEEP_WEEKLY=4
OMI_BACKUP_KEEP_MONTHLY=6
```

Days, weeks and months are counted back from the newest backup, in UTC. Only files named
`omi-backup-*` are ever deleted. `python3 omi-host/backup/omi_backup.py prune --dry-run`
(with `OMI_BACKUP_DIR` set) shows what would go.

### The other way: cron or a systemd timer on the host

Use this if you would rather not run another container, or if you want `gpg`, `restic` or
`rclone`, which are on the host and not in the image. Leave `OMI_BACKUP_ENABLED` unset and
call `backup.sh` yourself. It reads `.env`, finds the `omi-data` volume and quiesces the
same way. It needs `python3` on the host. Where there is none (stock Unraid), it
runs the same script in a throwaway omi-local container instead.

cron (Unraid: *User Scripts* plugin, schedule `30 3 * * *`):

```
30 3 * * *  root  bash /path/to/omi-host/backup.sh >> /var/log/omi-backup.log 2>&1
0 5 1 * *   root  bash /path/to/omi-host/backup-verify.sh >> /var/log/omi-backup.log 2>&1
```

systemd (`/etc/systemd/system/omi-backup.service` and `.timer`):

```
[Unit]
Description=Omi backup
RequiresMountsFor=/mnt/nas/omi-backups
[Service]
Type=oneshot
ExecStart=/bin/bash /path/to/omi-host/backup.sh
```

```
[Timer]
OnCalendar=*-*-* 03:30
Persistent=true
[Install]
WantedBy=timers.target
```

Then `systemctl enable --now omi-backup.timer`. Pick one approach. Running both takes two
backups a night.

## 3. Test your backups

A backup you have never restored is a hope. Once a month (on `OMI_BACKUP_VERIFY_DAY`,
default the 1st; 0 turns it off) the `omi-backup` service does a test restore. Run one by hand
any time:

```
bash omi-host/backup-verify.sh            # the newest backup
bash omi-host/backup-verify.sh --keep     # and leave the unpacked copy for a look
```

It unpacks the backup into a scratch folder (in `OMI_BACKUP_DIR`, or
`OMI_BACKUP_VERIFY_TMP`), verifying every checksum twice. Then it starts a throwaway
Firestore emulator and Redis on that copy. Nothing touches the running stack. It reads back:

```
Test restore of omi-backup-20261125T182800Z.tar.gz
  PASS  restore into a scratch directory (checksums verified twice)
  PASS  Firestore export present
  PASS  Firestore read back: conversations / tasks / users   7 conversations, 9 tasks, 0 users
  PASS  calendar items match the backup                      11 calendars, 11 events, 8 tasks
  PASS  vector store matches the backup                      0 embeddings in 0 SQLite file(s)
  PASS  stored files (audio, profiles) match the backup      0 files, 0 B
  PASS  Redis loads the snapshot                             59 keys
Result: OK
```

(That is the demo stack from `demo/backup_demo.sh`, which has no recordings or vectors.
Accounts live in the Auth emulator, so "users" counts only Firestore profile documents.)

The report is also saved in `OMI_BACKUP_DIR/verify/`. The health page shows the last
result, and a failed test restore turns "Last backup" yellow. The scratch copy needs as much
free space as one unpacked backup and is deleted afterwards.

## 4. Restore

### After the server's disk died

1. Install the new disk, then put Docker, this repository and `omi-host/.env` back. If
   `.env` was not in the backup, rebuild it from `.env.example` and your password manager.
2. Mount the NAS share again (step 1).
3. Build the images and create the volume without starting the stack:
   `cd omi-host && docker compose build && docker compose create`. This creates an empty
   `omi-data` volume.
4. Check the backup: `bash omi-host/restore.sh --dry-run`. It verifies the archive's
   checksum and every file's checksum, then says what it would restore.
5. Restore: `bash omi-host/restore.sh latest`. Or name an archive:
   `bash omi-host/restore.sh /mnt/nas/omi-backups/omi-backup-20261124T033000Z.tar.gz`.
6. Start: `bash omi-host/run-omi-local-host.sh`. The emulator imports the restored export,
   Redis loads its snapshot, Radicale serves the calendars. Phones stay signed in because
   the login key came back too.
7. Check: sign in on the phone, open a conversation, sync DAVx5 once, and look at
   the health page.

### Going back to an earlier day on a working server

```
cd omi-host && docker compose down       # the volume stays
bash restore.sh omi-backup-20261120T033000Z.json --force
bash run-omi-local-host.sh
```

`restore.sh` refuses to write into a volume that holds data unless you pass `--force`.
With `--force` it does not delete the current data. It moves it into
`.pre-restore-<time>/` inside the volume. Delete that folder once the restored stack looks
right: `docker run --rm -v omi-host_omi-data:/data alpine rm -rf /data/.pre-restore-<time>`.

`restore.sh` also refuses while omi-local is running. Restoring under a live emulator would be
overwritten by its next export.

Every checksum is verified before anything is written, and again as each file is
written. A damaged archive, a file that does not match the manifest, or a path that points
outside the volume stops the restore with nothing changed.

## Encryption (optional)

The archive holds your conversations, recordings and login keys. If the NAS is shared or
the copy leaves the house, encrypt it with [age](https://age-encryption.org) (in the image)
or gpg (host runner only).

1. Make a key pair on a machine **other than the server** (your laptop):
   `age-keygen -o omi-backup.key`. It prints the public key, `age1...`.
2. In `.env`: `OMI_BACKUP_ENCRYPT=age` and `OMI_BACKUP_AGE_RECIPIENTS=age1...`.
3. Keep `omi-backup.key` safe and off the server: a password manager, a USB stick in a
   drawer. Without it, the backups cannot be read by anyone, including you.

To restore or test, copy the key to the server for the occasion and point at it:
`OMI_BACKUP_AGE_IDENTITY=/root/omi-backup.key bash omi-host/restore.sh latest`.

With encryption on you can include `.env` too: `OMI_BACKUP_INCLUDE_ENV=1`. To get it back,
pass `--config-dir` to restore, which writes `.env` there and refuses to overwrite an
existing one without `--force`. Without encryption, `.env` is always left out, and each
backup warns about it.

gpg works the same way with `OMI_BACKUP_ENCRYPT=gpg` and `OMI_BACKUP_GPG_RECIPIENT=<key id>`,
using the host's keyring. Use the cron/systemd setup above for it.

## Offsite copy (optional)

A second copy away from the house protects against fire and theft. Both tools run on the
host, with the cron/systemd setup above, and copy each new archive and its `.json`:

- **restic** to a [rest-server](https://github.com/restic/rest-server) on a second NAS or a
  friend's machine: `OMI_BACKUP_OFFSITE=restic`,
  `OMI_BACKUP_RESTIC_REPOSITORY=rest:http://192.0.2.21:8000/omi`, and restic's own
  `RESTIC_PASSWORD_FILE=/root/.restic-omi`. Run `restic init` once.
- **rclone** to any remote you configured with `rclone config`:
  `OMI_BACKUP_OFFSITE=rclone`, `OMI_BACKUP_RCLONE_REMOTE=nas2:omi-backups`.

A failed offsite copy is a warning; the local backup still counts. Retention on the
offsite side is that tool's job (`restic forget --keep-daily 7 ...`).

The containers may only reach the LAN ([NETWORK.md](NETWORK.md)). A cloud target is
reachable only from the host, which is another reason these run there.

## Settings

| Setting | Default | |
|---|---|---|
| `OMI_BACKUP_ENABLED` | off | `1`: `run-omi-local-host.sh` starts the `omi-backup` service |
| `OMI_BACKUP_DIR` | | Where backups go (host path). Required. |
| `OMI_BACKUP_TIME` | `03:30` | Daily, in `TZ` |
| `OMI_BACKUP_KEEP_DAILY` / `_WEEKLY` / `_MONTHLY` | `7` / `4` / `6` | Retention |
| `OMI_BACKUP_VERIFY_DAY` | `1` | Day of the month for the test restore; `0` = never |
| `OMI_BACKUP_VERIFY_TMP` | `OMI_BACKUP_DIR` | Scratch space for the test restore |
| `OMI_BACKUP_ENCRYPT` | `none` | `age` or `gpg` |
| `OMI_BACKUP_AGE_RECIPIENTS` / `_RECIPIENTS_FILE` | | age public keys |
| `OMI_BACKUP_AGE_IDENTITY` | | age private key, only where you restore |
| `OMI_BACKUP_GPG_RECIPIENT` | | gpg key id |
| `OMI_BACKUP_INCLUDE_ENV` | `0` | `1` = include `.env` (needs encryption) |
| `OMI_BACKUP_OFFSITE` | | `restic` or `rclone` |
| `OMI_BACKUP_RESTIC_REPOSITORY`, `OMI_BACKUP_RCLONE_REMOTE` | | Their targets |
| `OMI_BACKUP_QUIESCE` | `auto` | `docker`, `flag`, `cmd` (`OMI_BACKUP_QUIESCE_CMD`) or `none` |
| `OMI_BACKUP_RUNNER` | `auto` | Host scripts: `host` (python3 here) or `docker` (in the image) |
| `OMI_BACKUP_GZIP_LEVEL` | `6` | Lower is faster. Audio barely compresses at any level. |

## Try the whole cycle without risking anything

`omi-host/demo/backup_demo.sh` runs everything on a throwaway stack, with no Docker. It fills the
stack with the CalDAV demo's users, conversations, tasks and calendars, backs it up while it
runs, deletes its data, restores onto an empty directory, starts the stack again and
compares what the user sees. Then it runs the test restore, and the watchdog's alert
check (HEALTH.md). See its header for what it needs.
