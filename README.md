# Healthcare Newsfeed

A weekly healthcare-news digest for aspiring doctors, published to a Telegram channel.

Fourteen sources are polled daily, deduplicated and ranked; once a week the best
dozen items are assembled into a structured issue and posted. The audience is
pre-med and medical-school applicants, so selection favours what actually helps
at interview — ethics, health policy, global health and new treatments — over
breaking-news volume.

> **Status: complete end to end.** All fourteen sources fetch and persist
> daily, a week's candidates cluster, rank and fill the issue's sections, and
> `newsfeed publish` renders the issue and posts it to the channel. Preview
> any week with `newsfeed publish --dry-run`, which needs no bot token. What
> is left is a judgement call rather than a gap — see [Roadmap](#roadmap).

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

Each signal is a small named function with a documented range, and the weights
at the top of `score.py` are the whole tuning surface. That matters more than
sophistication: when an issue carries the wrong story, the only useful question
is which signal put it there.

`pipeline/dedupe.py` runs first. Its exact pass — canonical URLs — already
happens on every insert; the near pass groups titles that tell one story
within three weeks of each other. Three weeks reads generous for a weekly
digest, and the sources argue for it: WHO files a fresh report on the same
outbreak every week or two, and NEJM ran one HIV trial a fortnight before The
Lancet's companion paper. Narrower, and an issue carries the same epidemic
twice. Allocation then takes one item per cluster, so the rest of that story
cannot come back in another section.

Sections are filled in two passes over the template — each gets its `min`
before any gets its second choice. One pass would let an early greedy section
take an item a later thin one was relying on, which is the crowding-out the
per-section quotas exist to prevent.

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

`ItemDefaultUrl` is a bare slug, and the base path it hangs off differs per
collection — `https://www.who.int/news/item` for news, and
`https://www.who.int/emergencies/disease-outbreak-news/item` for outbreak news.
Prefixing with `https://www.who.int` alone gives a 404. Both collections are
archives rather than windows, so `$top` without the ordering returns an
arbitrary page: unordered, the first three records came back dated 2017, 2020
and 2016. The adapter therefore checks the ordering it asked for actually took
effect, rather than storing a decade-old backlog as though it were this week.

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

The rule is one line of code rather than a convention to remember:
**licence sets a ceiling, the section's style sets the ask, and the extract
is the smaller of the two.**

| Section style | Asks for | link-only | CC / public domain |
|---|---|---|---|
| `headline` | nothing | title + link | title + link |
| `short` | 180 ch | 180 ch | 180 ch |
| `long` | 400 ch | **200 ch** | 400 ch |
| `extract` | 900 ch | **200 ch** | 900 ch |

So the same explainer slot carries 900 characters of a Conversation article
and 200 of a STAT one, without either section needing to know which source
filled it. A summary trimmed below 60 characters is dropped entirely — three
words and an ellipsis is worse than a headline and a link.

---

## What gets posted

An issue arrives as an ordered burst of messages rather than one, because
Telegram caps a message at 4,096 characters. Splits fall between items and
repeat the section heading with `(cont.)`, so a reader landing on the second
message still knows which block they are in. Nothing is truncated after the
fact: each item is composed to fit the room it has, because cutting assembled
HTML lands inside a tag and Telegram rejects the whole message rather than
the broken span.

### Publishers put their own boilerplate first

Two shapes of it, and the renderer removes both:

- **The journals lead with a citation.** Nature Medicine ships
  `Nature Medicine, Published online: 24 August 2026; doi:10.1038/…` and then
  the abstract. Printed as-is that reads as broken wherever it appears, so
  the citation is stripped and what follows is the summary. NEJM's feed is
  citation and *nothing else* — 87 characters where the description belongs —
  so nothing remains and the item renders title + link, which is the right
  answer for it. It falls out of the general rule rather than needing a
  special case.
- **The Conversation leads with its hero image's credit.** Every article
  opens `New Africa/Shutterstock.com …`, and the explainer section carries
  more text than any other, so it would open on a photo agency. A credit is
  matched only with its `/` separator — a bare agency name would fire on a
  wire report attributed to Reuters.

Both run at render time, not in the adapter: a `RawItem` is the item as
found, and stripping upstream would remove evidence the scorer ranks on.

### Failures

A digest is several messages, and `publish` writes to the store only once all
of them have landed. `mark_published` is what stops an item ever being
carried again, so recording a half-posted issue would retire the items in the
sections that never arrived — invisibly and permanently. A duplicate post is
visible and a human can delete it; a lost ethics section is neither. So a
partial failure records nothing, says how far it got, and exits non-zero.

Flood limits are waited out rather than dropped: Telegram answers 429 with a
`retry_after`, and half a digest is worse than a slow one. A rejected message
— bad HTML, a bad token — is not retried, because it will fail identically
however many times it is sent.

The bot token is in the URL, not a header, so every error out of `telegram.py`
is redacted before it reaches a log.

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
| `newsfeed verify` | Check feed health (wraps `tools/verify_feeds.py`). |
| `newsfeed build` | Assemble the issue for a week and print it. Writes nothing. |
| `newsfeed publish` | Build the issue, post it, and record what went out. Run weekly. |
| `python tools/verify_feeds.py` | Check feed health. |

```
$ newsfeed poll
SOURCE             STATUS   FETCHED  NEW  DETAIL
------------------------------------------------
statnews           ok            20   20
bbc_health         ok            52   50
nejm               blocked        -    -  nejm: HTTP 403
nature_med         failed         -    -  nature_med: HTTP 500  (tolerated)
who_dons           skip           -    -  polled 6.2h ago, every 24h

12/14 sources polled, 318 new items, 1 not yet due
```

`--only KEY…` polls named sources, `--force` ignores `poll_hours`, `--dry-run`
fetches without writing, and `--db` overrides `$NEWSFEED_DB`.

**A dead source does not stop the others, but it does colour the exit code.**
The split is the one `verify_feeds.py` already makes: a 401/403/429 is the host
refusing this particular egress address — NEJM does it to Actions runners — so
it is reported and tolerated, while a 404 or an unparseable response is the
source being broken and fails the run, because a daily schedule that quietly
stops collecting looks exactly like a quiet week. `--strict` fails on blocks
too; `tolerate_failure: true` on a source keeps a known-flaky host out of the
exit code entirely.

Due-ness comes from `poll_hours` against the store's record of when each source
last succeeded, so a run repeated within the day is nearly free. A *failed*
poll does not start that clock — a broken source is retried on the next run
rather than waiting out its interval.

```
$ newsfeed publish
This Week in Medicine — Issue 12: 14 of 173 candidates, 2 message(s)
issue 12 published to @channel: 2 message(s), 14 items recorded
```

`--dry-run` renders the issue to stdout and posts nothing — it needs no bot
token, so it works before the channel exists. `--week` publishes a past week,
`--issue` overrides the numbering, and `--config`, `--template` and `--db`
behave as they do for `build`.

**`build` and `publish` assemble the same issue.** They share one function, so
what `build` prints with its scores is what `publish` posts; the difference is
that `build` shows the ranking and `publish` shows the formatting. Preview
with `publish --dry-run` when the question is how the issue reads, and with
`build` when it is why an item is in it.

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
format; `tests/fixtures/README.md` records where each came from.

Two rules the adapter and store divide between them, worth knowing before
changing either:

- **`RawItem` is the item as found.** The adapter strips markup and normalises
  dates, and nothing else — tracking parameters included. Ranking and licence
  limits happen later, so an adapter that filtered would remove evidence the
  pipeline needs.
- **The store's identity is the canonical URL**, from `pipeline/dedupe.py`.
  Inserts are append-only: a re-poll never rewrites `first_seen`, or the item
  captured on Monday would slide into next week's issue.

### Layout

```
config/
  sources.yaml        14 sources: weights, sections, licences, retention data
  digest.yaml         section order, headings, per-section quotas
src/healthcare_newsfeed/
  models.py           Source, RawItem, Item, Section, Digest, Licence
  config.py           YAML loading and validation
  store.py            SQLite persistence (items · issues · polls)
  sources/            base.py (protocol) · rss.py · who.py
  pipeline/           dedupe.py · score.py · select.py
  digest/             template.py (section specs) · render.py (Telegram HTML)
  telegram.py         Bot API client — sendMessage, retries, redaction
  cli.py              poll · build · publish · verify
tools/
  verify_feeds.py     feed health checker
tests/                offline, fixture-driven
```

---

## Roadmap

- [x] Source research and live verification
- [x] Feed health checker
- [x] Source and digest configuration
- [x] RSS/Atom adapter — all three feed formats
- [x] SQLite store — canonical-URL identity, weekly window
- [x] WHO OData adapter — both collections
- [x] `newsfeed poll`: config loading and the daily run
- [x] Dedupe, scoring, section selection
- [x] Telegram rendering and publishing

Open, and a judgement call rather than a gap:

- [ ] Decide Singapore coverage — scrape MOH's Isomer pages, or keep relying
      on The Conversation Indonesia as the regional signal
- [ ] Wire up a store that outlives the runner before trusting the schedules
      (see [Deployment](#deployment))
