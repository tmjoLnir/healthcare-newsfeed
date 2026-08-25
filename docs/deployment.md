# Deployment: taking it live

Everything needed to get from a green test suite to a digest landing every
Sunday evening. There is nothing to provision — the deployment target is
GitHub Actions in this repository, and the work is credentials, one bootstrap,
and two rehearsals.

The short version: **the only irreversible step is the bootstrap in §2.4.** It
is the sole path that creates an empty store, and every scheduled run
afterwards refuses to start from empty on purpose. If a later run says the
asset is missing, the asset was deleted — re-bootstrapping would overwrite real
history with nothing. [docs/persistent-store.md](persistent-store.md) is the
companion to this note: it covers the store's sizing and recovery, this one
covers standing the whole thing up.

---

## 1. What "deploying" means here

Nothing is hosted anywhere. Three workflows are already committed and
scheduled; a runner wakes on a cron, restores the database, does its job, and
puts the database back.

| Workflow | Schedule (UTC) | Local (SGT) | Does |
|---|---|---|---|
| `poll.yml` | 02:00 daily | 10:00 daily | Fetches all 18 sources into the store |
| `publish.yml` | Sunday 11:00 | Sunday 19:00 | Builds the issue and sends the DM |
| `ci.yml` | every push and PR | — | `ruff`, `pytest`, and a live feed-health gate |

Cron is evaluated in UTC; the SGT column is what the reader experiences.

The one piece of state is the SQLite store, and it outlives the runner as a
**Release asset** — a prerelease tagged `store` holding one file,
`newsfeed.db.gz`. `tools/store_sync.sh` restores it before each run and uploads
it after. No external database, no account beyond this one, about 9 MiB for a
year of history.

Both scheduled workflows share the `newsfeed-store` concurrency group, because
that file is read-modify-write and two overlapping runs would lose whichever
finished first.

---

## 2. The sequence

### 2.1 Create the bot and capture the chat id

In Telegram: message [@BotFather](https://t.me/botfather), send `/newbot`, and
keep the token. Then open the chat that should receive the digest and send the
bot `/start`.

That second half is not optional. A bot cannot write to a chat that has not
written to it first — the reason `telegram.py` pairs that failure with the fix
rather than passing Telegram's bare `Forbidden` through.

Read the numeric chat id back out of the bot's own updates:

```bash
export TELEGRAM_BOT_TOKEN='123456:AA…'
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" \
  | jq '.result[-1].message.chat.id'
```

You want the number it prints. An `@username` is the channel form and fails
with `chat not found`.

### 2.2 Add the two repository secrets

**Settings → Secrets and variables → Actions**, *New repository secret*, twice.
Names must match exactly; `publish.yml` reads these and nothing else.

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | The token from §2.1 |
| `TELEGRAM_CHAT_ID` | The number from §2.1 |

Secret values are write-only — nobody can read one back, including whoever set
it. The *Updated* timestamp on that page is the only signal available. If it
predates the switch away from channels, overwrite it.

### 2.3 Confirm Actions can write to the repo

**Settings → Actions → General.** Both scheduled workflows declare
`permissions: contents: write`, which they need to upload the store back to its
Release. Check that Actions is enabled and that nothing at the organisation
level caps the token below write. No secret is needed here — they use the
`GITHUB_TOKEN` Actions issues automatically.

### 2.4 Bootstrap the store

**Actions → poll → Run workflow**, with **bootstrap** ticked. Or:

```bash
gh workflow run poll.yml -f bootstrap=true
```

This is the only path that will ever create an empty database, and it also
creates the `store` prerelease that every later run reads and writes.

Do it once. `store_sync.sh restore` fails without `STORE_BOOTSTRAP=true`
because an empty database and a quiet news week are indistinguishable
downstream, and one of them publishes.

### 2.5 Read the first poll's summary

**Actions → the run you just started.** Expect it to end like this:

```
16/18 sources polled, 318 new items
store_sync: saved 318 items (+318), 1.2M to store/newsfeed.db.gz
```

**Sixteen, not eighteen, is correct.** NEJM and Annals answer GitHub's shared
egress addresses with 403 while serving normally elsewhere. `poll` classifies
that as *blocked* rather than *failed*, so it does not break the run.

Then check **Releases**: a prerelease tagged `store` should now exist with
`newsfeed.db.gz` attached.

### 2.6 Let the store fill

The daily poll takes over. Seven days of polling is not a prerequisite for the
first issue — most feeds carry between 8 and 52 items of back-history, so the
first poll alone captures items published over the preceding days. Give it two
or three days before publishing, so the weekly window has depth to rank across.

### 2.7 Rehearse with a dry run

**Actions → publish → Run workflow**, with **dry_run** ticked. It renders the
issue into the run's log and posts nothing, writes nothing, records nothing.
Read the log for filled section headings, a character count under 4,096, and
the item count.

**A dry run does not test Telegram.** It swaps in `DryRunClient`, which blanks
the token and never opens a connection, so it proves the issue assembles — not
that the secrets can reach a chat. The first time the credentials are exercised
is the live run.

### 2.8 Go live

Either wait for the Sunday cron, or dispatch **publish** with `dry_run`
unticked. The run ends with the line that confirms delivery:

```
issue 1 published to chat 987654321: 1 message(s), 12 items recorded
```

A manual live run consumes that week's items exactly as the scheduled one
would: `mark_published` retires them, and they are never carried again. That is
correct behaviour rather than a side effect to work around, but it does mean
the next issue is built from what arrives after it.

---

## 3. The three things that bite

**Deleting the store Release.** It looks like a stray prerelease with a junk
asset. It is the entire history. Five feeds retain under a week, so whatever
rolled off upstream since the last poll exists in that file and nowhere else.

**A stale chat id.** If `TELEGRAM_CHAT_ID` still holds an `@name` from the
channel era, the Sunday run fails with `chat not found` — *after* assembling
the issue, so nothing is lost and nothing is delivered. The error names the fix,
but only in a run log nobody reads on a Sunday evening.

**Two sources are permanently quiet.** NEJM and Annals are blocked from Actions
runners by IP reputation, so the issue is assembled from sixteen sources.
Nothing announces this beyond the poll's summary line — a source blocked every
day is a source that is not in the digest. Restoring them means a residential
or proxied egress address, or a self-hosted runner.

---

## 4. When something fails

| Log line | What it means | Do |
|---|---|---|
| `chat not found` | Chat id is an `@name`, or a typo | Overwrite the secret with the number from §2.1 |
| `bot can't initiate conversation` | The chat never messaged the bot | Open the chat, send `/start`, re-run |
| `bot was blocked by the user` | The chat blocked the bot | Unblock, send `/start`, re-run |
| `no 'newsfeed.db.gz' in release 'store'` | The asset was deleted — *not* a first run | Recover it from the Release history. Never re-bootstrap over it |
| `store shrank from N to M items` | The file does not descend from the restored one | Investigate before the next poll; the upload was correctly refused |
| `restored store is unusable` | The asset is corrupt | Recover from the Release history; bootstrapping past it publishes an empty store |
| `no candidates stored for …` | Publish ran against an empty or unpolled store | Confirm the daily poll is green before the next Sunday |
| `nejm: HTTP 403` | Expected — egress reputation, not a broken feed | Nothing. Tolerated by design |

A partial post is the one failure that needs a human rather than a re-run:
`publish` records nothing when the burst fails halfway, so a re-run reposts what
already went out. Delete those messages from the chat first.
