# The persistent store: what it has to hold, and where it can live

The roadmap's last open engineering item is *"wire up a store that outlives the
runner before trusting the schedules."* A fresh GitHub Actions runner starts
with an empty `data/newsfeed.db`, which defeats daily polling. This note
measures what the store actually has to hold and checks the free options
against it.

The short version: **size is never the binding constraint.** A year of history
is 28 MiB, against 5–10 GiB free tiers. What decides the choice is durability
and blast radius — losing the file mid-week silently loses the issue, because
five feeds retain less than seven days.

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

## 5. Recommendation

**Cloudflare R2 with whole-file sync, if a card on file is acceptable.** It
costs nothing at this volume for any plausible lifetime of the project, keeps
`store.py` untouched, has no egress charge to worry about as history grows, and
the restored artefact is an ordinary SQLite file you can pull down and open.

**Otherwise a GitHub Release asset**, which needs no third-party account, no
card, and no new secret beyond the token Actions already has.

**Turso if a real remote database is wanted** — it is the only listed option
where the driver swap is small, and its free tier has three orders of magnitude
of headroom on both rows and bytes.

Two things to wire up whichever is chosen, because both failure modes are
silent:

- A `concurrency:` group on the poll and publish workflows. Every file-shaped
  option is read-modify-write, and two overlapping runs would lose whichever
  finished first.
- A run that fails loudly when the restore finds nothing. An empty database is
  currently indistinguishable from a genuinely quiet week — which is the exact
  hazard daily polling exists to avoid.

**Do not rely on `actions/cache`.** The 7-day eviction is not a tail risk here:
it is a coin flip on whether the store survives the first time anything
interrupts the schedule, and it fails without saying so.

---

## 6. Reproducing these numbers

The field sizes come from parsing `tests/fixtures/` with `RssAdapter.parse()`
and `WhoODataAdapter.parse()`. The growth table comes from inserting
fixture-derived prose into a real `Store` at the per-source weekly rates implied
by the retention windows in `config/sources.yaml` and the `CHARS` column of
`tools/verify_feeds.py` — 186 items/week in total, against the README's
observed ~175. Re-measure the inputs with `python tools/verify_feeds.py`.
