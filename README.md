# Healthcare Newsfeed

A weekly healthcare-news digest for aspiring doctors, published to a Telegram channel.

Fourteen sources are polled daily, deduplicated and ranked; once a week the best
dozen items are assembled into a structured issue and posted. The audience is
pre-med and medical-school applicants, so selection favours what actually helps
at interview — ethics, health policy, global health and new treatments — over
breaking-news volume.

> **Status: scaffolding.** Configuration, source verification and the feed checker
> work today. The pipeline modules are specified stubs. See [Roadmap](#roadmap).

---

## The weekly issue

```
This Week in Medicine — Issue 12
──────────────────────────────────────────
🔬 Story of the week      1 item,  with context
📊 From the journals      2-4 items (NEJM · Lancet · JAMA · Nature Medicine)
💡 Explainer              1-2 items (full text, republishable)
⚖️  Ethics corner          0-1 item
🌍 Global health watch    0-2 items (WHO)
🏥 Policy                 0-1 item
📌 Also worth reading     3-6 headlines
```

Sections have their own quotas rather than competing for one global ranking. A
heavy news week therefore cannot crowd out the ethics slot — the section this
audience benefits from most, and the one with the thinnest supply. Sections with
a `min` of 0 are omitted entirely when nothing qualifies; a missing block beats a
weak filler item.

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

Two schedules, deliberately decoupled: **polling is daily, publishing is weekly.**

### Why polling is daily

Feeds are windows, not archives. Measuring how much history each source actually
holds turns up a hazard:

| Source | Items held | Retention window |
|---|---|---|
| MedPage Today | 20 | **3 days** |
| JAMA Online First | 14 | **4 days** |
| Nature Medicine | 8 | **4 days** |
| KFF Health News | 10 | **5 days** |
| STAT Health | 20 | **6 days** |
| The Conversation (UK) | 25 | 10 days |
| NEJM | 37 | 123 days |
| BBC Health | 52 | 126 days |

Five sources turn over in under a week. A weekly poll would miss whatever rolled
off in between — invisibly, because a feed that dropped items looks exactly like a
quiet week. So the poll runs daily and the store holds the candidates until
publication. `data/newsfeed.db` is load-bearing, not a cache.

Re-measure any time with `python tools/verify_feeds.py`.

### Selection, not filtering

About 175 items arrive each week and roughly 12 are published — a 15:1 cut.
Keyword rules cannot carry that ratio, so `pipeline/score.py` ranks on source
weight, topic fit, durability (explainers hold their value across a week;
breaking news does not, and is penalised accordingly), cluster size and
readability. `pipeline/select.py` then fills each section under its quota,
never repeating an item carried by an earlier issue.

---

## Sources

Fourteen sources, all verified reachable on 2026-08-25.

| Source | Format | Items/week | Summary text | Licence |
|---|---|---|---|---|
| STAT — Health | RSS 2.0 | 20 | 700 ch | link only · paywalled |
| MedPage Today | RSS 2.0 | 20 | 247 ch | link only |
| BBC Health | RSS 2.0 | 22 | 107 ch | link only |
| The Conversation — UK Health | Atom | 17 | 6,270 ch | **CC-BY-ND** |
| The Conversation — AU Health | Atom | 12 | 6,380 ch | **CC-BY-ND** |
| The Conversation — Indonesia | Atom | 17 | 7,463 ch | **CC-BY-ND** |
| The Lancet | RSS 1.0 | 16 | 558 ch | link only · paywalled |
| JAMA — Online First | RSS 2.0 | 14 | 188 ch | link only · paywalled |
| NEJM | RSS 1.0 | 13 | 87 ch | link only · paywalled |
| Nature Medicine | RSS 1.0 | 8 | 359 ch | link only · paywalled |
| BMJ Journal of Medical Ethics | RSS 2.0 | 0-2 | 5,376 ch | link only |
| KFF Health News | RSS 2.0 | 10 | 6,862 ch | **CC, republishable** |
| WHO — News | OData JSON | ~6 | none | public domain |
| WHO — Disease Outbreak News | OData JSON | ~1 | 1,251 ch | public domain |

Three of these need handling that differs from the rest:

**WHO publishes no usable RSS.** Every documented feed path returns 404, and the
one URL that still resolves is abandoned — 25 items spanning over a year. Live
data comes from a Sitecore OData API, which defaults to an *unsorted* page and so
must be queried with an explicit ordering:

```
https://www.who.int/api/news/newsitems?$orderby=PublicationDateAndTime desc&$top=20
```

The news collection returns no body text and relative URLs, so those items render
as title + link. Outbreak news does carry a summary. Handled by `sources/who.py`.

**NEJM supplies no summary, and blocks datacenter IPs.** Its feed carries an
87-character citation string where the description belongs, so NEJM items render
title + link only. It also returns 403 to GitHub Actions runners while serving
normally from other networks — so the deployed poller may need a residential or
proxied egress address to reach it.

**The ethics blog is bursty.** Roughly 1-2 posts a week on average, but it can
fall silent for a fortnight — which is why the ethics section may be empty rather
than padded. At a weekly cadence this cadence is a fit; at a daily one it was not.

### Sources that do not work

Documented so they are not retried in good faith:

| Source | Why |
|---|---|
| **The BMJ** | Cloudflare returns 429/403 to datacenter IPs; `feeds.bmj.com` fails TLS; *BMJ Opinion* has been dead since January 2022 |
| **Medscape** | Cloudflare bot challenge |
| **MOH Singapore** | No RSS — the site runs on Isomer; every feed path 404s |
| **NUS Medicine**, **Duke-NUS** | Imperva/Incapsula bot protection |
| **LKC Medicine NTU** | Gateway 502 |

Singapore-institution coverage has **no RSS path at all** — four sources, four
different failure modes. The Conversation's Indonesian edition is the nearest
verified regional signal. Covering MOH properly would mean scraping its static
Isomer pages, which is tractable but a separate decision.

BMJ blocks datacenter IPs outright, and NEJM does the same to GitHub Actions
runners — so expect some publishers to treat any shared egress address this way.
`tools/verify_feeds.py` therefore separates the two cases: a 401/403/429 is
reported as `BLOCKED` and does not fail the sweep, while a 404, an unparseable
response or an empty feed is a real `FAIL`. Pass `--strict` to treat blocks as
failures too. This keeps the CI feed check a genuine gate instead of noise.

---

## Republishing

Licence drives how much text each item may carry, and the renderer enforces it:

- **Public domain** (WHO) — quote freely.
- **CC-BY-ND** (The Conversation) and **KFF** — full extracts permitted with
  attribution; both publish complete article text in-feed.
- **Everything else** — headline, a short summary *in your own words*, and a link.
  Do not republish article bodies from the trade press or the journals.

Paywalled sources are labelled inline so readers know before they click.

---

## Getting started

```bash
git clone <this repo> && cd healthcare-newsfeed
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -e ".[dev]"

cp .env.example .env      # then fill in the two Telegram values
```

Create the bot with [@BotFather](https://t.me/botfather), add it to your channel
as an administrator with *Post messages* permission, and put the token and
channel name in `.env`.

Check every source is reachable before doing anything else:

```bash
python tools/verify_feeds.py
```

```
SOURCE             HTTP  ITEMS NEWEST       7D  CHARS  VERDICT
statnews           200      20 2026-08-24   20    700  ok — 6d window, daily poll required
medpage            200      20 2026-08-24   20    247  ok — 3d window, daily poll required
...
14/14 sources healthy
```

It exits non-zero if an enabled source fails, so it can gate a deployment.

### Commands

| Command | Effect |
|---|---|
| `newsfeed poll` | Fetch every due source into the store. Run daily. |
| `newsfeed build` | Assemble the current issue without posting. |
| `newsfeed publish --dry-run` | Render the issue to stdout. |
| `newsfeed publish` | Post the weekly issue to Telegram. |
| `python tools/verify_feeds.py` | Check feed health. |

### Deployment

`.github/workflows/` ships three workflows: `poll` (daily 02:00 UTC / 10:00 SGT),
`publish` (Sunday 11:00 UTC / 19:00 SGT) and `ci`.

One thing to wire up before relying on them: **the store must outlive the
runner.** A fresh GitHub Actions runner starts with an empty database, which
defeats the whole point of polling daily. Point `NEWSFEED_DB` at hosted Postgres
or Turso, sync the SQLite file from object storage, or — least robust —
use `actions/cache`, which is evicted after 7 days of disuse and so only holds
while the daily poll keeps it warm.

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

### Layout

```
config/
  sources.yaml        14 sources: weights, sections, licences, retention data
  digest.yaml         section order, headings, per-section quotas
src/healthcare_newsfeed/
  models.py           Source, RawItem, Item, Section, Digest, Licence
  config.py           YAML loading and validation
  store.py            SQLite persistence
  sources/            base.py (protocol) · rss.py · who.py
  pipeline/           dedupe.py · score.py · select.py
  digest/             template.py · render.py
  telegram.py         Bot API client
  cli.py              poll · build · publish · verify
tools/
  verify_feeds.py     feed health checker (working)
tests/                offline, fixture-driven
```

---

## Roadmap

- [x] Source research and live verification
- [x] Feed health checker
- [x] Source and digest configuration
- [ ] RSS and WHO OData adapters
- [ ] SQLite store and daily poll
- [ ] Dedupe, scoring, section selection
- [ ] Telegram rendering and publishing
- [ ] Decide Singapore coverage — scrape MOH, or rely on The Conversation ID
