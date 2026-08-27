# Design notes

Why the pipeline works the way it does. The [README](../README.md) says what it
is; this says why. Each section here was written when the decision was made, and
is the place to start when changing that part.

- [Why polling is daily](#why-polling-is-daily)
- [Selection, not filtering](#selection-not-filtering)
- [Sources that need special handling](#sources-that-need-special-handling)
- [Republishing](#republishing)
- [What gets posted](#what-gets-posted)

---

## Why polling is daily

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

---

## Selection, not filtering

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

## Sources that need special handling

Most sources are a URL and a weight in `config/sources.yaml`. These five are not.

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
between 0.5 MB and 7.5 MB depending on the cache. Handled by `sources/isomer.py`.

The cap, not the range, is what bounds a poll — and it is set against how the
agency publishes rather than against a span. MOH's Parliamentary QAs arrive in
same-day bursts of twenty or more, so 40 records reaches back three weeks where
the ~11/week average would suggest a month. Three weeks is deliberate:
`window()` selects on when an item was *stored*, so everything the first poll
returns becomes a candidate for that week's issue, and `select()` has no score
floor — a stale item still fills an optional section when nothing competes for
it. At 40 the oldest item a first poll can introduce scores about 0.74 on
recency; at 200 it was four months old and scored 0.0.

**Which is why the cap is a source field rather than a constant.** A second
agency arrived on the same platform — HSA, added 2026-08-27 — publishing about
3 items a week against MOH's 11, and the same 40 records that span three weeks
of MOH span ninety days of HSA. `max_items` is therefore set per row: MOH takes
the adapter's 40, HSA takes 12, and both reach back about four weeks. A third
Isomer listing should count its own output rather than inherit either.

Nothing else in the adapter is agency-specific. The item base and the slug
prefix records hang off both come from the source's own `url`, so covering
another Isomer listing means a config row and no code.

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

