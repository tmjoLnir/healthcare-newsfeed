# Healthcare Newsfeed

A weekly healthcare-news digest for aspiring doctors, delivered as a Telegram DM.

Eighteen sources are polled daily, deduplicated and ranked; once a week the best
dozen items are assembled into a structured issue and posted. The audience is
pre-med and medical-school applicants, so selection favours what actually helps
at interview — ethics, health policy, global health and new treatments — over
breaking-news volume.

> **Status: complete end to end.** All eighteen sources fetch and persist
> daily, a week's candidates cluster, rank and fill the issue's sections, and
> `newsfeed publish` renders the issue and sends it to the chat. Preview
> any week with `newsfeed publish --dry-run`, which needs no bot token. What
> is left is a judgement call rather than a gap — see [Roadmap](#roadmap).

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

Quotas are what a section is worth carrying, not what will fit: the issue is
budgeted to one Telegram message, and the weakest items above each section's
`min` are dropped until it does. A thin week publishes fewer than the maxima
above; a heavy one does too.

Sections have their own quotas rather than competing for one global ranking. A
heavy news week therefore cannot crowd out the ethics slot — the section this
audience benefits from most, and the one with the thinnest supply. Sections with
a `min` of 0 are omitted entirely when nothing qualifies; a missing block beats a
weak filler item. *Qualifies* includes being recent: past its `min`, a section
takes nothing staler than `issue.min_recency`.

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

The two passes also decide where the **recency floor** applies. `window()`
selects on when an item was *stored*, not when it was written, so the first
poll of an archive-backed source lands months of history looking as current as
this morning's. Scoring decays it — but a low score still wins a section that
nothing else is competing for, which is how a four-month-old ministry release
came within one message-budget trim of the policy block. So the `min` pass
ignores the floor, and the `max` pass enforces it: guaranteed slots always
fill, optional ones only with something recent enough to belong in a weekly
digest. `issue.min_recency` is that line, on the same 1.0-to-0.0 scale
`score.py` decays over sixty days; 0.5 is about thirty-three days, comfortably
below the oldest item a real week actually published.

Clusters catch a story filed twice. A story that runs all week needs
something else: the DRC Bundibugyo outbreak reached one week's candidates as
23 items — a WHO situation report, a Nature Medicine case report, a Lancet
comment on civil society, a BBC vaccine trial — which share a subject and
almost no vocabulary. The closest pair of those scores 0.08 against
`dedupe.py`'s 0.50 threshold, and loosening that is not the answer, because
clustering closes transitively: a rule slack enough to link them chained
that week's candidates into one 142-item blob. So `select.py` caps how many
items in an issue may carry the same *subject* — a word that names what an
item is about. The count is per word and never transitive, which is what
stops it chaining: two items sharing a subject with a third, but nothing with
each other, are two subjects and not one. Two, by default: enough to cover a
big week properly and still be about more than one thing.

Which word names a subject is `_GENERIC`'s job, not a frequency threshold's.
The two interleave — in one real week "ebola" appeared in 20 of 324 titles
and "vaccine" in 12, so no cut through that ordering keeps the first and
drops the second. Worse, a proportional cut is at its tightest exactly when
the cap matters most, since a story word is common *because* its story is
running: at a 0.06 ceiling "ebola" missed by a single title, the cap never
fired, and the issue carried five reports of one outbreak. So the threshold
sits above the whole band as a backstop against runaway words, and the list
carries the distinction — fields of medicine, and the regulators and
administrations that name *who acted* rather than what happened. Three
unrelated stories touched the FDA in one issue; that is not one subject.

The cap's two slots go to the subject's best items, allocated in score order
before any section fills. Spending them as the sections fill instead gives
them to whichever block asks first, which is not the same thing: `journals`
has a `min` of 2 and `global_health` a `min` of 0, so the two Ebola slots
went to a Lancet comment and a MedPage summary while the WHO outbreak report
— the highest-scoring item on the subject, and the reason that section exists
— was refused. The cap held, and threw away the best item to do it.

---

## Sources

Eighteen sources, all verified reachable on 2026-08-25.

| Source | Format | Items/week | Summary text | Licence |
|---|---|---|---|---|
| STAT — Health | RSS 2.0 | 20 | 700 ch | link only · paywalled |
| MedPage Today | RSS 2.0 | 20 | 247 ch | link only |
| BBC Health | RSS 2.0 | 22 | 107 ch | link only |
| The Conversation — UK Health | Atom | 17 | 6,270 ch | **CC-BY-ND** |
| The Conversation — AU Health | Atom | 12 | 6,380 ch | **CC-BY-ND** |
| The Conversation — Indonesia (Kesehatan) | Atom | ~2 | 7,275 ch | **CC-BY-ND** |
| Annals, Academy of Medicine Singapore | RSS 2.0 | ~1 | 13,360 ch | **CC-BY-NC-SA** · blocked from CI |
| The Lancet | RSS 1.0 | 16 | 558 ch | link only · paywalled |
| The Lancet Regional Health — Western Pacific | RSS 1.0 | ~3.5 | 550 ch | link only |
| The Lancet Regional Health — Southeast Asia | RSS 1.0 | ~2 | 452 ch | link only |
| JAMA — Online First | RSS 2.0 | 14 | 188 ch | link only · paywalled |
| NEJM | RSS 1.0 | 13 | 87 ch | link only · paywalled |
| Nature Medicine | RSS 1.0 | 8 | 359 ch | link only · paywalled |
| BMJ Journal of Medical Ethics | RSS 2.0 | 0-2 | 5,376 ch | link only |
| KFF Health News | RSS 2.0 | 10 | 6,862 ch | **CC, republishable** |
| MOH Singapore | Next.js page | ~11 | none | link only |
| WHO — News | OData JSON | ~6 | none | public domain |
| WHO — Disease Outbreak News | OData JSON | ~1 | 1,251 ch | public domain |

Three of them are the regional signal, added after the survey in
[docs/asia-sources.md](docs/asia-sources.md): Singapore's own journal and the
two Lancet regional titles, joined by The Conversation's Indonesian *health*
section in place of its edition-wide feed. Annals is the one source whose
licence is neither CC-BY-ND nor link-only — CC-BY-NC-SA carries a share-alike
term, so it would need downgrading to link_only if the digest were ever
monetised.

Four others need handling that differs from the rest:

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

**MOH publishes no feed, and does not need one.** `/rss`, `/feed.xml` and
`/newsroom/rss.xml` all 404 — the site runs on Isomer Next. But the newsroom
listing page embeds its whole 8,367-item index in the Next.js flight payload,
newest first, each record carrying a real publication date, a category and a
title. The index begins 4.3% into a 7.5 MB page and is ordered newest first, so
the poller asks for the first 500 KB of it — about six months of history.

That range is an optimisation rather than a contract. MOH sits behind
CloudFront, which answers a cache hit with the whole page and no
`Accept-Ranges`: eight consecutive requests measured on 2026-08-25 all returned
200 and 7.5 MB, where the same request had returned 206 earlier that day. So
the adapter reads at most the newest 40 records whichever size arrives, parses
a partial page and a whole one identically, and the daily transfer swings
between 0.5 MB and 7.5 MB depending on the cache. Handled by `sources/moh.py`.

The cap, not the range, is what bounds a poll — and it is set against how MOH
publishes rather than against a span. Parliamentary QAs arrive in same-day
bursts of twenty or more, so 40 records reaches back three weeks where the
~11/week average would suggest a month. Three weeks is deliberate: `window()`
selects on when an item was *stored*, so everything the first poll returns
becomes a candidate for that week's issue, and `select()` has no score floor —
a stale item still fills an optional section when nothing competes for it. At
40 the oldest item a first poll can introduce scores about 0.74 on recency; at
200 it was four months old and scored 0.0.

MOH itself is reachable from an Actions runner — verified in CI on 2026-08-25,
which was granted the byte range the same day a residential path was refused
it. So it does not join NEJM and Annals in needing a proxied egress address.

Two things follow. MOH sets headlines in capitals, which would shout among
every other source's sentence case, so the adapter recases them — best-effort,
since capitalising the source destroyed the difference between an acronym and
an ordinary word. And items carry no summary at all: the index has none, and
MOH's [Terms of Use](https://www.moh.gov.sg/terms-of-use/) forbid reproducing
site contents without written permission, so the body text on the item pages is
deliberately left alone. MOH items render as title and link, like WHO news.

**NEJM supplies no summary, and blocks datacenter IPs.** Its feed carries an
87-character citation string where the description belongs, so NEJM items render
title + link only. It also returns 403 to GitHub Actions runners while serving
normally from other networks — so the deployed poller may need a residential or
proxied egress address to reach it.

**Annals is blocked from Actions runners too.** Verified in CI on 2026-08-25:
the feed serves normally from an ordinary network but Cloudflare answers the
runner with 403, so `newsfeed poll` reports it blocked and it contributes
nothing to the issue until egress is sorted. Two of the seventeen sources now
need that proxied address, not one — worth weighing before adding a third
Cloudflare-fronted publisher. `poll` classifies 401/403/429 as *blocked* rather
than *failed*, so neither source fails the daily run.

**The ethics blog is bursty.** Roughly 1-2 posts a week on average, but it can
fall silent for a fortnight — which is why the ethics section may be empty rather
than padded. At a weekly cadence this cadence is a fit; at a daily one it was not.

### Sources that do not work

Documented so they are not retried in good faith:

| Source | Why |
|---|---|
| **The BMJ** | Cloudflare returns 429/403 to datacenter IPs; `feeds.bmj.com` fails TLS; *BMJ Opinion* has been dead since January 2022 |
| **Medscape** | Cloudflare bot challenge |
| **NUS Medicine** | WordPress with feeds disabled — 500, `{"code":"wp_die","message":"No feed available."}` |
| **Duke-NUS** | Host reachable again as of 2026-08-25, but no feed exists at any path |
| **LKC Medicine NTU** | Gateway 502 |

Singapore *institutions* have no RSS path at all — three schools, three failure
modes. MOH is the exception, and it is a configured source: it publishes no feed
either, but `sources/moh.py` reads its newsroom index out of the rendered
page (see below).
[docs/asia-sources.md](docs/asia-sources.md) has the survey the regional sources
came out of, and [`config/candidates-asia.yaml`](config/candidates-asia.yaml)
re-runs the sweep in one command.

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

So the same `long` slot carries 400 characters of a Conversation article and
200 of a STAT one, without the section needing to know which source filled
it. A summary trimmed below 60 characters is dropped entirely — three words
and an ellipsis is worse than a headline and a link.

The shipped template asks `short` of the explainer, so every section but the
story of the week and the ethics corner now runs at one length. That is an
editorial choice rather than a licence one, and it has a consequence worth
stating: 180 sits below the link-only ceiling of 200, so in a `short` section
a CC-licensed article and a paywalled one render identically. `extract`
remains available for a section meant to carry real article text — nothing
ships using it.

---

## What gets posted

**The issue arrives as a single message.** `max_messages: 1` in the template
is what makes that true, and it is a budget on the issue rather than on the
splitter: sections fill to their quotas as usual, then the weakest items
above each section's `min` are dropped until the whole issue renders inside
one message. This week that was six items of seventeen.

Fitting is measured, not estimated. What an item costs is its title, its URL
and its summary — none of which a quota in the config can see — so the issue
is rendered, trimmed by one item, and rendered again. Tuning quotas until an
issue happened to fit would hold only until a week of longer headlines.

Items dropped for space are simply not published, so they keep their place
among next week's candidates. The section floors are not negotiable: a
budget that ignored them would empty the ethics corner to make room for a
fourth journal paper, which is the crowding-out the quotas exist to prevent.
When the floors alone will not fit, the issue runs long and `publish` says
so on stderr rather than breaking one.

Raise `max_messages`, or drop it entirely, and an issue too big for one
message becomes an ordered burst instead: splits fall between items and
repeat the section heading with `(cont.)`, so a reader landing on the second
message still knows which block they are in. Nothing is truncated after the
fact either way: each item is composed to fit the room it has, because
cutting assembled HTML lands inside a tag and Telegram rejects the whole
message rather than the broken span.

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
  opens `New Africa/Shutterstock.com …`, and it is the explainer section
  those articles fill, so the block would open on a photo agency — at 180
  characters the credit is a larger share of the item, not a smaller one.
  A credit is matched only with its `/` separator — a bare agency name would
  fire on a wire report attributed to Reuters.

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

Create the bot with [@BotFather](https://t.me/botfather), then open the chat
that should receive the digest and send the bot `/start`. **A bot cannot write
to a chat that has not written to it first**, and the chat id is a number
rather than an `@name`, so read it back from the bot's own updates:

```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" \
  | jq '.result[-1].message.chat.id'
```

Put that number and the token in `.env`. Nothing here needs a channel or
administrator rights — the digest goes to one chat, which is all a single
reader needs; the same two variables would address a group or a channel
unchanged if that ever changes. The scheduled publish reads them as the
repository secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

Check every source is reachable before doing anything else:

```bash
python tools/verify_feeds.py
```

```
SOURCE             HTTP  ITEMS NEWEST       7D  CHARS  VERDICT
statnews           200      20 2026-08-24   20    700  ok — 6d window, daily poll required
medpage            200      20 2026-08-24   20    247  ok — 3d window, daily poll required
...
18/18 sources healthy
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

16/18 sources polled, 318 new items, 1 not yet due
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
issue 12 published to chat 987654321: 2 message(s), 14 items recorded
```

`--dry-run` renders the issue to stdout and posts nothing — it needs no bot
token, so it works before the bot exists. `--week` publishes a past week,
`--issue` overrides the numbering, and `--config`, `--template` and `--db`
behave as they do for `build`.

**`build` and `publish` assemble the same issue.** They share one function, so
what `build` prints with its scores is what `publish` posts; the difference is
that `build` shows the ranking and `publish` shows the formatting. Preview
with `publish --dry-run` when the question is how the issue reads, and with
`build` when it is why an item is in it.

### Deployment

`.github/workflows/` ships three workflows: `poll` (daily 02:00 UTC / 10:00 SGT),
`publish` (Sunday 11:00 UTC / 19:00 SGT) and `ci`. This section is the reasoning;
[docs/deployment.md](docs/deployment.md) is the runbook — the ordered steps from
an unconfigured repository to the first issue landing, and what each failure in
the logs actually means.

A fresh runner starts with an empty database, which would defeat the whole
point of polling daily — so **the store outlives the runner** in a GitHub
Release. A prerelease tagged `store` holds one asset, `newsfeed.db.gz`, and
`tools/store_sync.sh` restores it before each scheduled run and uploads it back
afterwards. It costs nothing, needs no account beyond this one, and uses no
secret beyond the `GITHUB_TOKEN` Actions already issues — a year of history is
about 9 MiB compressed.

Start it once, by hand:

```bash
gh workflow run poll.yml -f bootstrap=true
```

That flag is the only way an empty store ever gets created. A scheduled run
that finds no asset **fails instead**, because an empty database and a quiet
news week are indistinguishable downstream — and one of them publishes. The
same instinct runs through the rest of it: a poll that fails partway still
saves what it captured, since those items are already gone from the feeds; a
store that comes back smaller than it left is refused, since nothing in the
pipeline deletes rows.

Both workflows share one `concurrency` group, because the file is
read-modify-write and two overlapping runs would lose whichever finished
first.

**Two sources need an egress address the runner does not have.** NEJM and
Annals both answer GitHub Actions runners with 403 while serving normally from
an ordinary network — confirmed in two consecutive CI runs on 2026-08-25.
`poll` classifies 401/403/429 as *blocked* rather than *failed*, so neither
breaks the daily run: they contribute nothing, and the issue is assembled from
the other fifteen. Restoring them means giving the poller a residential or
proxied egress address, or a self-hosted runner. Until then `poll`'s summary
line is where to notice it — a source blocked every day is a source that is not
in the digest, and nothing else will say so.

[docs/persistent-store.md](docs/persistent-store.md) has the sizing that
settled this, the free options that were compared, and how to operate it.

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
  sources.yaml        18 sources: weights, sections, licences, retention data
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
  store_sync.sh       carry the store between runs via a Release asset
docs/
  asia-sources.md     the regional source survey, and what was rejected
  deployment.md       standing it up: secrets, bootstrap, the first issue
  persistent-store.md store sizing, and the free options that fit it
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
- [x] A store that outlives the runner — a GitHub Release asset, sized and
      chosen in [docs/persistent-store.md](docs/persistent-store.md)
- [x] An MOH adapter — no feed exists, so `sources/moh.py` reads the newsroom
      index out of the page's Next.js payload, capped at the newest 40 records
- [x] Singapore and Asia coverage decided and shipped — Annals plus the two
      Lancet regional titles, and The Conversation Indonesia moved to its
      health section. Surveyed, measured and costed in
      [docs/asia-sources.md](docs/asia-sources.md), including what was
      rejected and why

Open, and judgement calls rather than gaps:

- [ ] Egress that reaches NEJM and Annals from an Actions runner. Both serve
      normally elsewhere and both answer the runner with `403`, so they
      contribute nothing to a published issue until the poller has a
      residential or proxied address. `poll` already classifies this as
      *blocked* rather than *failed*, so it costs nothing else
- [x] CNA evaluated — `www.channelnewsasia.com` was opened on 2026-08-26 and
      CNA was measured at last. **Rejected:** it publishes no health feed at
      any path (seven general feeds, none health), carries 1 health item in 40,
      and polling it left the built issue byte-identical. The Singapore
      general-news gap is closed as unfillable rather than open — the Straits
      Times, NUS Newsroom and CNA all fail the same way. See
      [docs/asia-sources.md](docs/asia-sources.md) third sweep
