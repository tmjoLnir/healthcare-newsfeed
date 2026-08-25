# The persistent store: what it has to hold, and where it can live

A fresh GitHub Actions runner starts with an empty `data/newsfeed.db`, which
defeats daily polling. This note measures what the store has to hold, checks
the free options against it, and records what was built.

The short version: **size is never the binding constraint.** A year of history
is 28 MiB, against 5–10 GiB free tiers. What decides the choice is durability
and blast radius — losing the file mid-week silently loses the issue, because
five feeds retain less than seven days. The store now lives in a GitHub
Release asset; [§5](#5-what-was-chosen-a-release-asset) is the part to read if
you are operating it rather than re-deciding it.

---

## 1. What has to survive

Reading the store's callers rather than guessing:

| What | Where | Why it must persist | Size |
|---|---|---|---|
| The week's candidates | `store.window()` reads a 7-day slice of `first_seen` | Five feeds turn over in under a week; anything not captured on the day is gone | ~186 items ≈ **0.55 MiB** |
| `polls.last_success` | `cli.is_due()` | Without it every run re-fetches every source, and `poll_hours` means nothing | 14 rows ≈ **2 KiB** |
| `issues` | `store.next_issue()` | Issue numbering | ~52 rows/year ≈ **2 KiB/year** |
| `items.published_in_issue` | `select()` | Never carry the same item twice | set on ~14 rows/week |

So the **functional floor is about 1 MiB** — one week of candidates plus two
bookkeeping tables. Everything beyond that is archive, and it is worth keeping
for two concrete reasons rather than sentiment: `publish --week` can rebuild a
past issue, and `score.py`'s weights are the whole tuning surface, which can
only be re-tuned against real history.

Note that `select()` only excludes already-published items *within the loaded
7-day window*, and `dedupe`'s 21-day `WINDOW` compares `published` dates inside
that same window. Neither reaches further back than `window()` hands it. The
archive is for humans, not for correctness.

---

## 2. How much, measured

Per-item field sizes, parsed from the captured responses in `tests/fixtures/`
with the real adapters — not estimated:

| Source | items | title | url | summary | row bytes |
|---|---:|---:|---:|---:|---:|
| `bbc_health` | 4 | 72 | 48 | 104 | 301 |
| `who_news` | 3 | 117 | 157 | 0 | 368 |
| `nature_med` | 3 | 104 | 50 | 378 | 611 |
| `who_dons` | 2 | 75 | 70 | 2,974 | 3,196 |
| `conversation_uk` | 3 | 99 | 128 | 6,180 | 6,490 |
| `kff` | 2 | 80 | 112 | 7,450 | 7,813 |

**Three sources carry 90% of the bytes.** KFF and The Conversation ship whole
articles, and the adapter keeps them because their CC licence is what lets
`render.py` republish a long extract; WHO DONs carry a ~3,000-character
summary. The other eleven sources average a few hundred bytes each.

Growing a real `Store` to a year at ~186 items/week, with prose sampled from
the fixtures so the compression ratio is honest:

| Elapsed | Items | On disk | gzip -9 |
|---|---:|---:|---:|
| 1 month | 741 | 2.2 MiB | 0.7 MiB |
| 3 months | 2,412 | 7.1 MiB | 2.3 MiB |
| 6 months | 4,821 | 14.1 MiB | 4.7 MiB |
| **1 year** | **9,645** | **28.1 MiB** | **9.3 MiB** |
| 2 years | 19,294 | 56.1 MiB | ~19 MiB |
| 5 years | 48,237 | 140.3 MiB | ~47 MiB |
| 10 years | 96,468 | 280.0 MiB | ~93 MiB |

**≈3.0 KiB per item on disk** including both indexes, ≈0.55 MiB/week,
≈28 MiB/year, compressing about 3.0×. `VACUUM` recovers under 1% — the store is
insert-only, so there is nothing to reclaim.

### Request volume

Worth stating because several free tiers meter operations rather than bytes:

| | Reads | Writes |
|---|---:|---:|
| Per poll (daily) | 14 due-checks + ~300 uniqueness probes | ~27 new items + 14 poll upserts |
| Per publish (weekly) | ~186-row window scan | ~14 updates + 1 issue insert |
| **Per month** | **~10,000 rows** | **~1,300 rows** |

Roughly 300 `INSERT OR IGNORE` are attempted daily (the sum of the feeds' page
sizes) and about 27 land; the rest are same-URL re-sightings. Eight short
sessions a week, one writer, no concurrency.

---

## 3. One constraint in the code

`NEWSFEED_DB` is typed `Path` in `cli.py:99` and goes straight to
`sqlite3.connect()` in `store.py:111`. **It cannot currently hold a database
URL.** That splits the options in two: those that keep a SQLite file and change
no code, and those that need `store.py` to grow a driver.

`store.py` also leans on `sqlite3.Row`, `executescript()`, `total_changes` and
`INSERT OR IGNORE`, so how far a swap reaches depends on how SQLite-shaped the
target is.

---

## 4. Free options

### Keep the file — no change to `store.py`

Restore the file at the start of a run, upload it at the end.

| Option | Free allowance | Fits? | The catch |
|---|---|---|---|
| **Cloudflare R2** | 10 GiB, 1M writes + 10M reads/mo, **zero egress** | ~350 years of history; 60 ops/month against 1M | Requires a card on file to activate, even on the free tier |
| **Backblaze B2** | 10 GiB, free egress up to 3× stored | Storage yes, egress no | Daily pulls are ~840 MiB/mo against ~84 MiB free at year-1 size → about **$0.008/month**. Cheap, not free |
| **Git branch in this repo** | 5 GiB repo (soft), 100 MiB/file (hard) | ~3.5 years raw, ~10 gzipped | No new account and no card. Force-push a single-commit orphan branch or history grows by 28 MiB a day |
| **GitHub Release asset** | 2 GiB/file | Decades | Same, without touching git history — replace one asset per run |
| **`actions/cache`** | 10 GiB, **evicted after 7 days unused** | Only while nothing interrupts | Any 7-day gap — a broken workflow, a spend freeze, the 60-day inactivity auto-disable — empties it silently. The failure mode is indistinguishable from a quiet news week |

### Swap the driver — `store.py` changes

| Option | Free allowance | Fits? | Work involved |
|---|---|---|---|
| **Turso / libSQL** | 5 GiB, 500M rows read + 10M written/mo | 175 years; we use 10k reads and 1.3k writes a month | Smallest. The `libsql` Python client is DB-API shaped, so `sqlite3.connect` → `libsql.connect` and a `total_changes` substitute. Free DBs hibernate after ~1h idle (1–5 s wake) and archive after 14 days hibernating — a daily poll keeps it live |
| **Neon Postgres** | 0.5 GiB per project | ~18 years | Dialect rewrite: `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`, `executescript`, `sqlite3.Row`, `total_changes` |
| **Supabase** | 500 MiB | ~18 years | Same rewrite, plus it **pauses after 7 days of inactivity** — the daily poll prevents that, but it is one more thing that has to keep working |
| **Cloudflare D1** | 5 GiB, 5M reads + 100k writes/day | Comfortably | Most work: HTTP API only, no Python DB-API driver |

---

## 5. What was chosen: a Release asset

**Implemented** — `tools/store_sync.sh`, wired into both workflows.

A prerelease tagged `store` holds one asset, `newsfeed.db.gz`. Each scheduled
run restores it before doing anything and uploads it back afterwards. It needs
no third-party account, no card, and no secret beyond the `GITHUB_TOKEN`
Actions already issues; a year of history is ~9 MiB against a 2 GiB per-asset
limit.

Cloudflare R2 remains the alternative if the store ever outgrows this — same
shape, same script structure, but it wants a card on file even on the free
tier. Turso is the one to reach for if a real remote database is ever wanted,
since it is the only option here whose driver swap is small.

**`actions/cache` was rejected.** Its 7-day eviction is not a tail risk: it is
a coin flip on whether the store survives the first time anything interrupts
the schedule, and it fails without saying so.

### How it behaves

```
newsfeed poll  ──▶  restore ──▶ poll ──▶ save
                       │                  │
                       │                  └─ always(), even on a failed poll
                       └─ refuses to start empty
```

| Situation | What happens |
|---|---|
| No asset, scheduled run | **Fails.** An empty store publishes as a quiet week |
| No asset, manual run with `bootstrap` | Starts empty, says so loudly |
| Asset corrupt | **Fails**, and does not offer to bootstrap past it |
| Poll fails on some sources | **Saves anyway** — see below |
| Restore failed | Save never runs; the good asset is left alone |
| Store came back smaller | **Refuses to upload** |

Three of those are worth the words they cost:

**A failed poll still saves.** `newsfeed poll` exits non-zero when a source
breaks, but by then it has already committed what the other thirteen returned
— and MedPage and JAMA will have rolled those items off within four days.
Discarding a day's captures because one host 404'd would cause exactly the loss
the daily schedule exists to prevent. So the save step is `if: always()`,
guarded by the restore having succeeded.

**A shrinking store is refused.** `items` is insert-only — `upsert()` is
`INSERT OR IGNORE` and nothing in the codebase deletes rows — so the count
cannot legitimately fall. If it has, the local file is not a descendant of the
restored one and uploading it would destroy history. The restore records the
count it handed over; the save checks it.

**Bootstrapping is explicit, once.** A scheduled run will not create an empty
store under any circumstances. That takes a manual `workflow_dispatch` with
the `bootstrap` box ticked, which is the only way the first run gets off the
ground — and the only thing standing between a lost asset and an issue built
from nothing.

The two workflows share one `concurrency: newsfeed-store` group with
`cancel-in-progress: false`. The file is read-modify-write, so overlapping runs
would lose whichever finished first; cancelling is worse than queueing, because
a cancelled run has already committed items to a store it will never upload.

### Operating it

```bash
# First run ever — creates the release with its first asset.
gh workflow run poll.yml -f bootstrap=true

# Look at what the schedules are actually holding.
gh release download store --pattern newsfeed.db.gz && gunzip newsfeed.db.gz
sqlite3 newsfeed.db 'SELECT source_key, count(*) FROM items GROUP BY 1'
```

The asset is data, not a software release: deleting it loses every item
captured since the last poll that upstream feeds have since dropped. The
release is marked prerelease so it stays out of "latest release".

---

## 6. Reproducing these numbers

The field sizes come from parsing `tests/fixtures/` with `RssAdapter.parse()`
and `WhoODataAdapter.parse()`. The growth table comes from inserting
fixture-derived prose into a real `Store` at the per-source weekly rates implied
by the retention windows in `config/sources.yaml` and the `CHARS` column of
`tools/verify_feeds.py` — 186 items/week in total, against the README's
observed ~175. Re-measure the inputs with `python tools/verify_feeds.py`.
