# Healthcare Newsfeed

A weekly healthcare-news digest for aspiring doctors, delivered as a Telegram DM.

Eighteen sources are polled daily, deduplicated and ranked; once a week the best
dozen items are assembled into a structured issue and posted. The audience is
pre-med and medical-school applicants, so selection favours what actually helps
at interview — ethics, health policy, global health and new treatments — over
breaking-news volume.

**Complete end to end.** Preview any week with `newsfeed publish --dry-run`,
which needs no bot token. Current state, blocked sources and open items live in
[docs/status.md](docs/status.md).

---

## The weekly issue

```
This Week in Medicine — Issue 12
──────────────────────────────────────────
🔬 Story of the week      1 item,  with context
📊 From the journals      2-4 items (NEJM · Lancet · JAMA · Nature Medicine)
💡 Explainer              1-2 items (CC-licensed, republishable)
⚖️  Ethics corner          0-1 item
🌍 Global health watch    0-2 items (WHO)
🏥 Policy                 0-1 item
📌 Also worth reading     3-6 headlines
```

Sections have their own quotas rather than competing for one global ranking, so
a heavy news week cannot crowd out the ethics slot — the section this audience
benefits from most, and the one with the thinnest supply. A `min` of 0 means the
section is omitted entirely when nothing qualifies; a missing block beats a weak
filler item. The issue is budgeted to one Telegram message, and the weakest
items above each section's `min` are dropped until it fits.

Edit `config/digest.yaml` to change the order, headings or quotas.

---

## How it works

```
   config/sources.yaml
           │
           ▼
   ┌───────────────┐   daily    ┌──────────┐
   │  adapters     │───────────►│  store   │   SQLite, one row per canonical URL
   │  rss · odata  │            └────┬─────┘
   └───────────────┘                 │ weekly
                                     ▼
                            ┌──────────────────┐
                            │ dedupe → score   │
                            │    → select      │
                            └────────┬─────────┘
                                     ▼
                            ┌──────────────────┐
                            │ render → publish │   Telegram Bot API
                            └──────────────────┘
```

Two schedules, deliberately decoupled: **polling is daily, publishing is
weekly.** Feeds are windows rather than archives — five sources turn over in
under a week — so the poll runs daily and the store holds candidates until
publication. `data/newsfeed.db` is load-bearing, not a cache.

About 175 items arrive each week and roughly 12 are published. That 15:1 cut is
a ranking problem rather than a filtering one: `pipeline/score.py` ranks on
source weight, topic fit, durability, cluster size and readability, and
`pipeline/select.py` fills each section under its quota, never repeating an item
an earlier issue carried.

Licence decides how much of an item may be republished — public domain and
CC-licensed sources can carry real extracts, everything else gets a headline, a
short summary in your own words, and a link.

**The reasoning behind all of it is in [docs/design.md](docs/design.md)** — why
the poll is daily, how ranking and the subject cap work, the sources that need
special handling, the republishing rule, and what happens when posting fails.

---

## Getting started

```bash
git clone <this repo> && cd healthcare-newsfeed
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -e ".[dev]"

cp .env.example .env      # then fill in the two Telegram values
```

Create the bot with [@BotFather](https://t.me/botfather), then open the chat that
should receive the digest and send the bot `/start`. **A bot cannot write to a
chat that has not written to it first**, and the chat id is a number rather than
an `@name`, so read it back from the bot's own updates:

```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" \
  | jq '.result[-1].message.chat.id'
```

Put that number and the token in `.env`. The scheduled publish reads them as the
repository secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

Check every source is reachable before doing anything else:

```bash
python tools/verify_feeds.py      # exits non-zero if an enabled source fails
```

### Commands

| Command | Effect |
|---|---|
| `newsfeed poll` | Fetch every due source into the store. Run daily. |
| `newsfeed verify` | Check feed health (wraps `tools/verify_feeds.py`). |
| `newsfeed build` | Assemble the issue for a week and print it with scores. Writes nothing. |
| `newsfeed publish` | Build the issue, post it, and record what went out. Run weekly. |

`--only KEY…` polls named sources, `--force` ignores `poll_hours`, `--dry-run`
fetches without writing, and `--db` overrides `$NEWSFEED_DB`. For `publish`,
`--dry-run` renders to stdout and needs no bot token, `--week` publishes a past
week and `--issue` overrides the numbering.

**A dead source does not stop the others, but it does colour the exit code.** A
401/403/429 is the host refusing this particular egress address, so it is
reported as *blocked* and tolerated; a 404 or an unparseable response is the
source being broken and fails the run, because a daily schedule that quietly
stops collecting looks exactly like a quiet week.

---

## Deployment

`.github/workflows/` ships three workflows: `poll` (daily 02:00 UTC / 10:00 SGT),
`publish` (Sunday 11:00 UTC / 19:00 SGT) and `ci`.

A fresh runner starts with an empty database, so **the store outlives the runner**
in a GitHub Release: a prerelease tagged `store` holds one asset,
`newsfeed.db.gz`, and `tools/store_sync.sh` restores it before each scheduled run
and uploads it back afterwards. A year of history is about 9 MiB compressed.
Start it once by hand with `gh workflow run poll.yml -f bootstrap=true` — that
flag is the only way an empty store is ever created, because a scheduled run that
finds no asset fails instead.

- [docs/deployment.md](docs/deployment.md) — the runbook, from an unconfigured
  repository to the first issue landing, and what each failure means
- [docs/persistent-store.md](docs/persistent-store.md) — store sizing, and the
  free options that were compared

---

## Development

```bash
pytest -q          # tests run offline against captured fixtures
ruff check .
```

Feed samples in `tests/fixtures/` are captured responses, so the suite needs no
network and stays stable when a publisher changes its output. The configured set
spans RSS 1.0 (RDF), RSS 2.0, Atom and OData JSON — keep at least one fixture per
format.

Two rules the adapter and store divide between them:

- **`RawItem` is the item as found.** The adapter strips markup and normalises
  dates, and nothing else — tracking parameters included. Ranking and licence
  limits happen later, so an adapter that filtered would remove evidence the
  pipeline needs.
- **The store's identity is the canonical URL.** Inserts are append-only: a
  re-poll never rewrites `first_seen`, or the item captured on Monday would slide
  into next week's issue.

### Layout

```
config/
  sources.yaml        18 sources: weights, sections, licences, retention data
  digest.yaml         section order, headings, per-section quotas
src/healthcare_newsfeed/
  models.py           Source, RawItem, Item, Section, Digest, Licence
  config.py           YAML loading and validation
  store.py            SQLite persistence (items · issues · polls)
  sources/            base.py (protocol) · rss.py · who.py · moh.py
  pipeline/           dedupe.py · score.py · select.py
  digest/             template.py (section specs) · render.py (Telegram HTML)
  telegram.py         Bot API client — sendMessage, retries, redaction
  cli.py              poll · build · publish · verify
tools/
  verify_feeds.py     feed health checker
  store_sync.sh       carry the store between runs via a Release asset
tests/                offline, fixture-driven
```

---

## Documentation

| Document | What it covers |
|---|---|
| [docs/status.md](docs/status.md) | **Where the project stands** — source inventory, what is blocked, what was rejected, open items, decision log |
| [docs/design.md](docs/design.md) | Why the pipeline works the way it does — polling cadence, ranking and the subject cap, special-cased sources, republishing, posting failures |
| [docs/asia-sources.md](docs/asia-sources.md) | The Singapore/Asia source survey: three sweeps, what was measured, what was rejected and why |
| [docs/deployment.md](docs/deployment.md) | Standing it up: secrets, bootstrap, the first issue |
| [docs/persistent-store.md](docs/persistent-store.md) | Store sizing, and the free hosting options that fit it |
