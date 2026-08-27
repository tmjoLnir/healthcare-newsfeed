# Singapore and Asian healthcare sources

Measured 2026-08-25 against the live web, using `tools/verify_feeds.py`'s
checks and — for the three candidates worth adding — the project's own
`sources/rss.py` adapter end to end. Five sweeps have run since; each is a
dated section below, and the [fifth](#fifth-sweep--2026-08-27-what-singapore-content-the-digest-actually-holds)
is the one to read for where things stand.

The question this answers: the digest publishes on `Asia/Singapore` time to a
Singapore audience, but every source in `config/sources.yaml` is UK, US, AU or
global. `conversation_id` is carried as "nearest verified SE Asia signal; not
health-only". What else is actually reachable?

Short answer: **three feeds are ready to add today, one Singapore source needs
a small adapter, and the Straits Times is a worse fit than it looks.**

---

## Ready to add — verified end to end

All three parse through `sources/rss.py` unchanged: correct dates, links and
summaries, no new adapter, no new dependency.

| Key | Feed | Items | Newest | 30d | Retention | Summary |
|---|---|---|---|---|---|---|
| `lancet_wpc` | `https://www.thelancet.com/rssfeed/lanwpc_online.xml` | 16 | 2026-08-21 | 15 | 37d | 550 ch |
| `lancet_sea` | `https://www.thelancet.com/rssfeed/lansea_online.xml` | 15 | 2026-08-24 | 8 | 299d | 452 ch |
| `conversation_id_health` | `https://theconversation.com/id/kesehatan/articles.atom` | 25 | 2026-08-20 | 9 | 77d | 7,275 ch |

**The Lancet Regional Health — Western Pacific and South-East Asia.** Same
publisher and same RSS 1.0 shape as the `lancet` source already configured, so
`entry_date`'s `dc:date` handling already covers them. The content is genuinely
regional rather than global work with an Asian author list — the current window
carries the Asia–Pacific cardiovascular disease burden 1990–2023, *H. pylori*
gastric-cancer prevention, infection-driven cancers in Asia, nasopharyngeal
carcinoma biomarkers, stillbirth risk in India, and antimicrobial stewardship in
Indian facilities. Between them they run ~5 items a week, which fits `journals`
and `global_health` without crowding NEJM and JAMA out.

One caveat: article pages return 403 to datacenter IPs even though the feeds
serve normally, so the licence could not be confirmed from here. Both are gold
open-access titles, so `licence: cc` is probably right — but start them at
`link_only`, which is correct either way, and only widen it after checking an
article page from an unblocked network.

**The Conversation — Indonesia, health section.** This is a straight upgrade to
the `conversation_id` row rather than an addition. The configured feed is the
edition-wide one, and its current window is mostly not health: Gen Z and
folklore, whether cats find play stressful, Borneo forest fires, aid funding in
Asia. The `kesehatan` section feed is health-only — extreme heat degrading drug
and vaccine supply chains, outcome-based hospital quality ratings, BPJS patients
and eroding clinical empathy, ADHD underdiagnosis in women.

The trade is volume for precision: ~3.9 items/week edition-wide against ~1.9 a
week health-only, but nearly all of the second number is usable where most of
the first is not. Same CC-BY-ND licence and same ~7,000-character full text, so
`digest/render.py` can still take a real extract. Note the copy is Indonesian —
that is already true of the configured feed, so this changes nothing, but it is
worth a deliberate decision rather than an inherited one.

There is no `/id/health/` path — that 404s. Only the Indonesian slug works.

---

## Singapore: MOH is reachable, with a small adapter

The source inventory recorded MOH as *"No RSS — the site runs on Isomer; every
feed path 404s"*. Both halves are still true — `/rss`, `/feed.xml` and `/newsroom/rss.xml`
all 404. But the conclusion drawn from it, that covering MOH means scraping
static pages, is now wrong: **the newsroom ships a complete machine-readable
index, and one partial HTTP request is enough to read it.**

`https://www.moh.gov.sg/newsroom/` is an Isomer Next (Next.js) page whose RSC
flight payload embeds all 8,367 newsroom items, newest first, each with a real
publication date, a category and a title:

```json
{"id":"/newsroom/<slug>","date":"$D2026-08-24T00:00:00.000Z",
 "plaintextTags":[{"category":"Category","selected":["Speeches"]}],
 "title":"SPEECH BY MR TAN KIAT HOW, ...","description":" "}
```

The full page is 7.5 MB, but the index begins 4.3% in and is sorted
newest-first, so a `Range: bytes=0-500000` request recovers the most recent
~175 items — around four months of history — in a fifteenth of the transfer.

**The range is not reliable, which the survey got wrong.** MOH sits behind
CloudFront, and a cache hit is answered with the whole page and no
`Accept-Ranges`: eight consecutive requests on 2026-08-25 all came back 200 and
7.5 MB, where the same request had returned 206 earlier the same day. The
adapter therefore treats the range as an optimisation and the item cap as the
contract — newest 40 records, whichever size arrives.
`tools/isomer_newsroom_probe.py` reports which of the two actually happened.

### Volume

| Category | Last 7d | Last 30d | Last 90d | Last 365d | ≈ per week |
|---|---|---|---|---|---|
| Press Releases | 0 | 3 | 12 | 62 | 1.2 |
| Parliamentary QA | 0 | 43 | 62 | 399 | 7.7 |
| Speeches | 1 | 9 | 22 | 121 | 2.3 |
| Forum Replies | 1 | 2 | 2 | 17 | 0.3 |
| **Total** | **2** | **57** | **98** | **599** | **11.5** |

Parliamentary QA arrives in bursts tied to sitting days — 43 of the last 30
days' 57 items — which is why the seven-day column looks so thin. For a weekly
issue the stream that matters is Press Releases plus Forum Replies, about 1.5
items a week. That is exactly the shape of a `min: 0` section: present most
weeks, absent without padding when Parliament is quiet.

### What the adapter does

Built as `sources/moh.py`, registered as the `moh_newsroom` adapter.
*(Renamed in the [seventh sweep](#seventh-sweep--2026-08-27-hsa-shipped-and-the-adapter-generalised) to `sources/isomer.py` and `isomer_newsroom`, once HSA turned out to need the same parser.)*

1. `GET https://www.moh.gov.sg/newsroom/` with `Range: bytes=0-500000`. The
   final `self.__next_f.push([1,"…"])` chunk is cut mid-string, so the parser
   ends on the last complete unit instead of anchoring on `"])` — and rejects a
   `"])` that turns out to be an escaped quote inside a string, which would
   otherwise silently drop every record after it.
2. Decode each chunk **as a JSON string, not with `unicode_escape`**. That was
   the one real trap: `unicode_escape` round-trips through latin-1 and turns
   every multi-byte character into mojibake — 2,210 corrupted bytes on the live
   page. The first version of `tools/isomer_newsroom_probe.py` had this bug; the
   probe now drives the adapter, so there is one parser rather than two.
3. Read records one at a time from the first `"items":[`, since the byte range
   leaves the array unclosed — parsing it whole would fail on every real fetch.
4. Recase the titles, which MOH sets in capitals. Best-effort: capitalising the
   source destroyed the acronym information, so `A&E`, `MOH`, `CHAS`,
   `MediSave` and friends are restored from a list and everything else comes
   back as ordinary title case. A headline that is not all capitals is left
   untouched, so it is a no-op if MOH ever changes house style.
5. Emit no summary. `description` is blank for every record, and MOH's Terms of
   Use forbid reproducing site contents, so the full body text on the item
   pages is deliberately not fetched. Items render as title and link, like WHO
   news items.

`tools/verify_feeds.py` checks it too, through the same adapter — a gate that
tested something other than what the poller does would not be one.

### Licence

MOH's [Terms of Use](https://www.moh.gov.sg/terms-of-use/) are restrictive:

> the Contents of this Web Site shall not be reproduced, republished, uploaded,
> posted, transmitted or otherwise distributed in any way, without the prior
> written permission of the Ministry of Health.

The Singapore Open Data Licence on the same page applies to *Datasets*, not to
site contents. So MOH is **`link_only`** — headline, an own-words summary and a
link. That is what the project already does for the trade press and the
journals, and `digest/render.py` enforces it, so nothing new is needed. It does
mean the full body text an item page carries may inform a summary but must not
be republished as an extract.

---

## Measured and rejected: The Straits Times

The Straits Times does serve RSS — `/news/singapore/rss.xml`,
`/news/asia/rss.xml`, `/news/world/rss.xml` and five more all return 200 with 50
items. There is no health section feed: `/news/health/rss.xml` and
`/news/science/rss.xml` return `{"error":"No rss feeds found"}`, and
`/tags/health/rss.xml` returns a well-formed but empty feed.

So using ST would mean keyword-filtering a general news feed. Measured against
the health keyword set the scorer already uses, that does not work:

| Feed | Health-ish hits | Feed spans |
|---|---|---|
| `/news/singapore` | 6 / 50 | 1d 20h |
| `/news/asia` | 4 / 50 | 1d 4h |
| `/news/world` | 4 / 50 | 14h |

And the hits are mostly not healthcare. The Singapore six were: a cyclist jailed
over a pedestrian's death, two people taken to hospital after traffic accidents,
one after a hotel kitchen fire, a helper jailed for assaulting an elderly woman,
and an air-quality reading nearing unhealthy. Court and accident reports that
mention a hospital, not health news. Only the Asia feed turned up a real story
("How big pharma targets China's waistline").

Two further problems compound it. The feeds hold **under two days** of history,
so the daily poll [design.md](design.md#why-polling-is-daily) defends would silently drop items — this would be
the only source needing a faster cadence. And ST is hard-paywalled, so items
render as headline and link anyway.

Verdict: high volume, near-zero healthcare signal, worst-in-set retention. Not
worth the row.

---

## Confirmed dead

Checked directly; do not retry without re-testing. One entry here was wrong —
see the Duke-NUS row — because a challenge page was read as a real response.
When re-testing, check the body, not just the status.

| Candidate | Result |
|---|---|
| `moh.gov.sg/rss`, `/feed.xml`, `/newsroom/rss.xml` | 404 — no feed anywhere (use the newsroom index above) |
| **Duke-NUS** | **Still Incapsula-blocked — corrected in the [fifth sweep](#correction-duke-nus-is-not-reachable-with-no-feed).** Every path answers with a 212-byte Incapsula challenge stub: `/allnews/rss` and `/newshub/rss` with a 200, which is the "HTML with a 200" this row used to report, and `/feed`, `/rss.xml` with a 404. The block never lifted, so whether a feed exists is unknown rather than answered |
| **NUS Medicine** | `medicine.nus.edu.sg/feed/` → 500, `{"code":"wp_die","message":"No feed available."}` — WordPress with feeds disabled. Re-confirmed 2026-08-27: a genuine `text/xml` origin response, unlike Duke-NUS above |
| `theconversation.com/id/health/articles.atom` | 404 — only the `kesehatan` slug exists |
| The Conversation topic feeds | Parse, but abandoned: `topics/singapore-1123` last published 812 days ago, `topics/southeast-asia-1211` 3,696 days ago |
| `straitstimes.com` health/science/lifestyle/tech RSS | 400 `{"error":"No rss feeds found"}` |
| WHO regional APIs | `who.int/southeastasia/api/news/newsitems` and the Western Pacific equivalent both 404. The HQ OData collection carries no region field — `Provider` is `OpenAccessDataProvider` for every record — so WHO regional output cannot be separated out. `feature-stories` and `commentaries` collections do not exist either |

---

## Not verifiable from this network — first sweep

*Superseded in part: seven of these were opened later the same day. See
[Second sweep](#second-sweep--2026-08-25-after-seven-hosts-were-opened) for
what they turned out to be — `annals.edu.sg` among them, now a configured
source.*

This session's egress policy answers `403` to CONNECT for most hosts, which is a
property of where the check ran, not of the feeds — the same distinction
`verify_feeds.py` already draws for NEJM and BMJ. These are the candidates worth
checking from an unblocked network, collected in
[`config/candidates-asia.yaml`](../config/candidates-asia.yaml):

```
python tools/verify_feeds.py config/candidates-asia.yaml
```

Blocked here: `channelnewsasia.com`, `asia.nikkei.com`, `scmp.com`,
`koreabiomed.com`, `japantimes.co.jp`, `timesofindia.indiatimes.com`,
`thehindu.com`, `bangkokpost.com`, `thejakartapost.com`, `e.vnexpress.net`,
`thestar.com.my`, `chinadaily.com.cn`, `focustaiwan.tw`, `asianscientist.com`,
`medicalchannelasia.com`, `healthcareasiamagazine.com`, `mobihealthnews.com`,
`healthcareitnews.com`, `biospectrumasia.com`, `asianews.network`,
`asiaresearchnews.com`, `a-star.edu.sg`, `healthhub.sg`, `hsa.gov.sg`,
`singhealth.com.sg`, `nuhs.edu.sg`, `ncid.sg`, `sgh.com.sg`, `nus.edu.sg`,
`ntu.edu.sg`, `annals.edu.sg`, `smj.org.sg`, `news.google.com`,
`pubmed.ncbi.nlm.nih.gov`.

`lkcmedicine.ntu.edu.sg` returned a gateway 502, matching the existing note in
[status.md](status.md#sources-that-do-not-work) — but through the same blocked path, so it is not a fresh confirmation.

The highest-value ones to check first are **CNA** (believed at the time to be
the only Singapore general-news outlet with documented per-section RSS,
including a mental-health section, and unpaywalled — the third sweep found no
such health feed exists), **Korea Biomedical Review** (English-language
healthcare trade press for Korea) and **Healthcare Asia / Medical Channel
Asia** (regional healthcare trade press, WordPress, so `/feed/` most likely
works).

---

## Second sweep — 2026-08-25, after seven hosts were opened

`annals.edu.sg`, `news.nus.edu.sg`, `healthcareasiamagazine.com`,
`medicalchannelasia.com`, `koreabiomed.com`, `news.google.com` and
`pubmed.ncbi.nlm.nih.gov` were opened. CNA was not, and remains the biggest
gap. Four of the newly reachable hosts serve working feeds; one earned a row.

### Annals, Academy of Medicine Singapore — added

`https://annals.edu.sg/feed/`. Singapore's own peer-reviewed journal, and the
single best regional source found: 10 items, ~1/week, 41-day window, **full
article text in-feed** (10,000–39,000 characters), and heavily Singapore and
South-East Asia weighted. The current window carries a bibliometric analysis of
stroke publications in South-East Asia, adaptive expertise in the region,
China's school myopia programme and dementia burden trends in China.

Licence is **CC BY-NC-SA 4.0**, open access since April 2023 and applied
retroactively. That is a better licence than most of the set, but not the same
shape as the CC-BY-ND the other `cc` sources carry: non-commercial fits a free
Telegram digest, but *share-alike* is a real condition. The row is `cc`, and
its note says to downgrade to `link_only` if the digest is ever monetised.

Roughly one item in ten is an administrative "Continuing Medical Education"
post with a 178-character body — the scorer deprioritises it naturally, and the
section quotas drop it, so it needs no special handling.

**One caveat found only after it shipped.** Annals sits behind Cloudflare and
answers GitHub Actions runners with 403 while serving normally from an ordinary
network — the CI feed sweep confirmed it on 2026-08-25. That is the same
position NEJM is in, and `poll` already classifies 401/403/429 as *blocked*
rather than *failed*, so it costs nothing and needs no `tolerate_failure`. But
it does mean Annals will contribute nothing to the published issue until the
poller has a residential or proxied egress address. Two of seventeen sources
now need one.

The block is worth understanding precisely, because it is not purely about IP:
`annals.edu.sg` serves a request with no User-Agent at all, and serves a browser
User-Agent from an ordinary network, but rejects `Python-urllib/3.11` outright.
`sources/base.py` already sends a browser User-Agent for exactly this reason, so
the remaining 403 from CI is IP reputation on top of that — the same thing BMJ
does to every datacenter address.

### Live, but rejected on review

The audience is pre-med and medical-school applicants and selection favours
ethics, policy, global health and new treatments. Liveness was never the bar.

| Candidate | Volume | Why not |
|---|---|---|
| **Healthcare Asia Magazine** (`/rss.xml`, not `/feed`) | ~21/wk | Investor and market trade press — urinalysis market sizing, OUE Healthcare's privatisation offer, CKD sales forecasts. Also malformed: `<title>` carries a summary sentence rather than a headline, and summaries are empty, so items would render as a stray fact plus a link |
| **Medical Channel Asia** | ~2.3/wk | Well-formed, full text, Singapore-based — but consumer wellness, not medicine. Pickleball injuries, coffee and body fat, dental anxiety. The best feed of its kind found; reconsider only if a consumer-health section is ever wanted |
| **NUS Newsroom** | ~19/wk | University-wide PR. One of seven sampled items was health-adjacent; the rest were arbitration centres, edge AI and horseshoe crabs. The Straits Times failure mode again |
| **Google News** (SG healthcare query) | ~18/wk | Genuinely on-topic, and the best fallback if CNA never opens. But item links are `news.google.com/rss/articles/CBMi…` redirects, not publisher URLs, which breaks the store's canonical-URL identity and would have the digest link to a redirector. Needs a URL-resolution step first |

### Still blocked

`koreabiomed.com` opened at the apex but `301`s to `www.koreabiomed.com`, which
did not — so every feed path under it still fails at CONNECT. It needs the
`www` host opened too.

**CNA remains the outstanding gap.** `www.channelnewsasia.com` is still blocked,
and it is the only Singapore general-news outlet with documented per-section
RSS, unpaywalled — precisely where the Straits Times failed. Nothing found in
either sweep replaces it.

*Superseded: CNA was opened and measured in the [third
sweep](#third-sweep--2026-08-26-cna-opened-and-measured), and rejected — the
per-section RSS turned out to be seven general feeds with no health section
among them.*

---

## Third sweep — 2026-08-26, CNA opened and measured

`www.channelnewsasia.com` was opened, and CNA was measured end to end for the
first time. **Verdict: rejected.** Not because it is noisy — the scorer handles
it — but because CNA publishes no health feed at all, and its general feeds
publish nothing.

### The catalogue is seven general feeds, and none is health

The whole premise of the CNA rows was that CNA has documented per-section RSS
"including a mental-health section". That is wrong, and this sweep is where it
was checked. `https://www.channelnewsasia.com/rss` lists exactly seven feeds:

| Feed | `category` | Notes |
|---|---|---|
| Latest News | *(none)* | site-wide |
| Asia | `6511` | verified |
| Business | `6936` | verified — 20/20 items under `/business` |
| Singapore | `10416` | verified |
| Sport | `10296` | verified — 20/20 items under `/sport` |
| World | `6311` | verified — 15/20 under `/world` |
| Today | `679471` | **dead — returns zero items** |

So the two ids already in `candidates-asia.yaml` were right. The `category`
parameter is genuinely honoured — a bogus id returns zero items rather than
falling back to the site-wide feed — but it takes numeric Drupal taxonomy ids
only, and no health id is discoverable. CNA does run health sections on the
website — `/mental-health`, `/news/healthmatters`, `/topic/wellness`,
`/today/mental-health-matters` — and **none of them has a feed**. Their pages
carry no taxonomy id in the markup, `/api/v1/rss-outbound-feed` is the only API
endpoint the site references, and it rejects `category=wellness`,
`category=health` and `category=mental-health` alike. There is no health feed
to find, so nothing here replaces the Straits Times gap after all.

### What the general feeds actually hold

| Feed | Items | Window | Rate | Summary | Health items |
|---|---|---|---|---|---|
| Singapore `10416` | 20 (capped) | **20.6h** | ~23/day | 166 ch median, no full text | **1 of 20** |
| Asia `6511` | 20 (capped) | **35.1h** | ~14/day | 91 ch median, no full text | **0 of 20** |

Both feeds are hard-capped at 20 items. The Singapore window is *shorter than
the poll interval*, so a daily poll drops items — the Straits Times problem
again, and worse.

The one health item in forty was "Doctor accused of causing patient's death by
cutting wrong arteries restricted from surgery" — a malpractice court report,
which is precisely what the Straits Times sweep rejected ("court and traffic
reports mentioning a hospital"). A keyword filter over the Singapore feed
matched 3 of 20, and two of those were false positives: a cocaine seizure
matching *drug*, and a desalination trial matching *pre-treat*. The rest of the
window is fashion, cashback promotions, an anime concert and a Mid-Autumn
Festival fair.

### It publishes nothing — measured, not estimated

Added to `sources.yaml` at the proposed weight 0.8 and polled for real
alongside all eighteen configured sources, then built:

```
$ diff issue_no_cna.txt issue_with_cna.txt
38c38
< 11 of 411 candidates published
---
> 11 of 431 candidates published
```

The issue is otherwise **identical**. CNA's best item of the twenty ranks
**146th of 431** candidates (score 2.200); the cut for this issue was rank 11
at 2.967, and rank 50 still scored 2.632. This is not a near miss that a quiet
week would flip.

Worth recording precisely because a smaller test suggested otherwise: scored
against `bbc_health` *alone*, CNA looked dangerous — four of the top ten, and a
higher mean than BBC Health — because its items are always maximally recent (the
feed turns over in 20 hours) and feature-written, so `recency` and `readability`
carry them. Against the real 431-candidate pool that advantage disappears
entirely. `topic_fit` leading the weights is doing its job; the two-source
comparison was the misleading measurement, not the scorer.

So the cost of adding CNA is ~150 items a week of general news in the store for
zero published items. Store size is [never the binding
constraint](persistent-store.md), so this is not a storage argument — it is
simply that the row would do nothing except make every future sweep re-litigate
it.

### On getting hosts opened

CNA arrived in two steps worth remembering: the apex `channelnewsasia.com` was
opened first, which bought nothing, because it only `301`s to
`www.channelnewsasia.com` — and a redirect target is a separate CONNECT, so the
policy still refused it. **An allowlist entry has to name the `www` host.**
`www.koreabiomed.com` is still in exactly that state, re-confirmed blocked here.

---

## Fourth sweep — 2026-08-27, every other route into CNA

The third sweep rejected CNA's seven general feeds. This one asks the follow-up
question: **is there any other way to get CNA health news?** Seven routes were
tried. None works, and the reason turns out to sit upstream of delivery.

### What `robots.txt` discloses

`https://www.channelnewsasia.com/robots.txt` is the useful find, and it was not
read in the earlier sweeps. It carries `Crawl-delay: 10`, a blanket
`Disallow: /api/*`, and then four explicit exceptions:

```
Allow: /api/v1/google-news-feed
Allow: /api/v1/sitemap-news-feed
Allow: /api/v1/sitemap-video-feed
Allow: /api/v1/sitemap-image-feed
```

Worth noting against the third sweep's verdict: `/api/v1/rss-outbound-feed` —
the endpoint behind all seven feeds on CNA's own `/rss` page — falls under
`Disallow: /api/*` and is *not* one of the exceptions. CNA publishes those feeds
for readers while disallowing them to crawlers. It does not change the rejection
(the feeds carry no health content either way), but a poller is a crawler, and
that is the more defensible reading.

### The routes, and why each fails

| Route | Result |
|---|---|
| `/api/v1/google-news-feed` | **Live, robots-allowed, and full article text** — Atom, 50 entries, median 2,472 characters of body. But it is site-wide "Latest News" with no category parameter, and its window is **9.3 hours** against ~129 items/day. A daily poll would capture 50 of ~129 and miss the rest. Today's 50: 21 sport, 13 business, 7 world, 0 health |
| `/api/v1/sitemap-news-feed` | The same 50 items in sitemap form, same 9.3-hour window. Adds `news:keywords`, but only on 18 of 50 |
| Scraping `/mental-health`, `/news/healthmatters`, `/topic/wellness` | **No dates at all** — no `datetime` attribute, no visible date, nothing to set `published` from. And they are evergreen hubs, not news streams: median node id ~3.0M on `/mental-health` against 6.26M–6.34M for articles published today, with `/news/healthmatters` topping out at 4.13M. Padded with `/advertorial/`, `/watch/`, `/listen/` and `/podcasts/` |
| Drupal JSON:API (`/jsonapi`) | 403, and `Disallow: /jsonapi/*` |
| Per-topic feeds (`/topic/wellness/rss`, `/mental-health/rss`) | 404 |
| `cnalifestyle.channelnewsasia.com` | Refused at CONNECT — a separate host from the allowlisted `www`, the apex/`www` trap again. Lifestyle content, so low value even if opened |
| Google News, `site:channelnewsasia.com` | See below — the closest thing to a route, and still no |

### Google News, scoped to CNA

One thing here is genuinely new and corrects the second sweep's note. Adding
**`when:7d`** to the query fixes the staleness that made the plain `site:` query
useless — without it the 100 results span 2020-09-05 to 2026-08-26 (a 2,181-day
window, ranked by relevance rather than date); with it, 100 items across 6 days.

It still fails, for two reasons:

1. **68 of the 100 are not journalism.** CNA938 radio segments dominate — "The
   Wellness Hour", "Mind Your Money" — alongside features about *Mediacorp's TV
   drama hospital set*. Of the 32 that remain, several are duplicates of one wire
   story (two on a Pakistan hospital fire, three on Imran Khan's hospital
   transfer).
2. **There is no publisher URL anywhere in the feed.** `<link>` and `<guid>` are
   opaque `CBMi…` ids that no longer decode; `<description>`'s anchor points at
   the same redirect; `<source url>` gives only `https://www.channelnewsasia.com`,
   the home page. Following the redirect returns HTTP 200 still on
   `news.google.com` — a Google interstitial, not a hop to CNA. The store's
   identity is the canonical URL, so this is fatal rather than inconvenient.

### The conclusion is upstream of delivery

Only one route is even technically viable — poll `google-news-feed` about four
times a day to cover its 9.3-hour window, and filter it for health. That would
cost a new adapter, a per-source poll cadence (`poll_hours: 6`, which
`sources.yaml` already supports) plus the workflow schedule to match, ~129 items
a day of ingest, and a keyword filter at the source, which cuts against
`score.py`'s "ranking, not filtering".

It is not worth building, and the reason is not the plumbing. CNA barely
produces the thing this digest wants. The Singapore feed carried 1 health item
in 20 and it was a malpractice court report; the site-wide feed carried 0 in 50;
today's news sitemap had exactly one article tagged with a health keyword and it
was *"What happens to men's skin after 35?"*; and the bulk of what a health
query does surface is radio programming. The third sweep already measured the
consequence end to end — CNA at weight 0.8 changed the built issue not at all.

**No further CNA route is worth trying.** Reopen this only if CNA ships a
health-section feed, which would show up on `/rss` and in `robots.txt`.

---

## Fifth sweep — 2026-08-27, what Singapore content the digest actually holds

The first four sweeps asked *what could be added*. This one asks the question
from the other end: with eighteen sources configured, **how much Singapore
health content is in the store, where does it come from, and which Singapore
domains are still unmeasured?**

All eighteen sources were polled into a scratch store on 2026-08-27 — 416
items, every source `ok`, including `nejm` and `annals_sg`, which serve
normally here and only fail from an Actions runner.

### The inventory

| | Items | Share |
|---|---|---|
| Published *by* a Singapore source (`moh_sg` 40 + `annals_sg` 10) | 50 | 12.0% |
| Mentioning Singapore anywhere in the other sixteen sources | **1** | 0.3% |
| Wider SEA / Asia-Pacific signal (`conversation_id`, `lancet_wpc`, `lancet_sea`, `lancet`, `who_news`) | 29 | 7.0% |

The single outside mention is The Conversation AU's "One Nation wants to cut
the tobacco tax", which cites Singapore as a comparison case for illicit
cigarettes. **Nothing else in 366 items from the non-Singapore sources refers
to Singapore at all.** That is the fourth sweep's conclusion arriving from the
other direction: the regional gap is not that Singapore content ranks poorly,
it is that no configured general or journal source produces any.

### Annals is Singapore-published, not Singapore-topical

Worth stating plainly, because a keyword scan says otherwise and the source
inventory can be read as promising more than it delivers. Seven of the ten
Annals items in the window match "Singapore" **only in the WordPress footer**
`The post … appeared first on Annals Singapore.` — a generator artifact, not
content. Of the three that mention Singapore in the body, all three do so as
one country among several:

| Item | Where Singapore appears |
|---|---|
| Stroke publications in Southeast Asia | in the ASEAN member-state list |
| Training for constraint in Southeast Asia | one worked example of a well-resourced system |
| Interpreting multivariable regression coefficients | a 2010 Singapore paper, cited in passing |

The rest of the window is China's school myopia programme, dementia burden in
China, and three case reports. So Annals earns its weight as **Singapore's own
peer-reviewed journal covering the region** — which is what the second sweep
actually measured — and MOH is the only source of Singapore-*specific* health
news the digest has. The [second sweep](#annals-academy-of-medicine-singapore--added)
overstated the case in calling it "heavily Singapore and South-East Asia
weighted"; South-East Asia, yes, Singapore, only by authorship.

### What reaches the reader

Two of the eleven items in the issue built from this poll are Singapore's:

| Rank | Score | Item |
|---|---|---|
| 6 / 416 | 3.108 | MOH — Treatment Guidelines for Children and Adolescents with Gender Dysphoria |
| 25 / 416 | 2.798 | Annals — Training for constraint in Southeast Asia |

That is the ratio working as designed: 12% of the store, 18% of the issue.
MOH's remaining 39 records rank 84th and below, because 31 of the 40 are
Parliamentary QA — the burst the survey predicted, and the reason `moh_sg`
sits in a `min: 0` section.

| MOH category | Records in the window |
|---|---|
| Parliamentary QA | 31 |
| Speeches | 5 |
| Forum Replies | 2 |
| Press Releases | 2 |

### Two defects the scan turned up in the Annals text

**Fixed: the WordPress footer was being stored as body text.** Every item from
the two WordPress feeds — Annals and the JME blog, 20 of 416 — carried
`The post <title> appeared first on <site>.` at the end of its summary. For a
long article that is invisible, since the extract is cut from the front. For
the shortest it is the whole extract: `Continuing Medical Education` has a
178-character body against the `journals` section's 180-character budget, so
it rendered as a digest block whose entire text was boilerplate. `sources/rss.py`
now drops the footer, anchored at the end so it cannot eat real prose;
`tests/fixtures/annals_sg_rss2.xml` pins it.

**Not fixed, and deliberately: Annals PDF-only items carry a stub, not full
text.** Four of the ten items in the window are published as a PDF and ship
only `This article is available only as a PDF. Please click on "Download PDF"
on top to view the full article.`, sometimes after an abstract — 178, 601, 979
and 1,099 characters against the 10,000–39,000 the source inventory quotes.
The inventory's "roughly one item in ten is an administrative CME post" is
therefore an undercount of the effect; it is four in ten, and three of them
are case reports rather than CME notices. No handling is needed all the same,
and this sweep measured why rather than assuming it: they rank 190th, 234th,
312th and 353rd of 416, so the quotas drop them without help.

One more, recorded so it is not re-reported as a bug: stored text can contain
literal `<sub>` and similar, because `clean_text` strips tags *before*
unescaping entities. That order is correct and should stay — swapping it makes
`p &lt; 0.05) but q &gt; 0.1` collapse to `p 0.1`, and medical abstracts are
full of exactly that. Escaped markup surviving as visible text is the cheaper
failure, and `digest/render.py` escapes on output, so it can never break a
Telegram send.

### Domains: 56 hosts probed

Every Singapore health, hospital, university, agency and news domain that has
appeared in this survey, plus the clusters and statutory boards it never
listed, re-probed from this session on 2026-08-27.

**Reachable** — CONNECT succeeds and the origin answers:

| Host | What it is | State |
|---|---|---|
| `annals.edu.sg` | Annals, AMS | configured source |
| `www.moh.gov.sg` | MOH newsroom | configured source |
| `www.straitstimes.com` | Straits Times | rejected, first sweep |
| `www.channelnewsasia.com` | CNA | rejected, third and fourth sweeps |
| `news.nus.edu.sg` | NUS Newsroom | rejected, second sweep |
| `medicalchannelasia.com` | Medical Channel Asia | rejected, second sweep |
| `healthcareasiamagazine.com` | Healthcare Asia | rejected, second sweep |
| `pubmed.ncbi.nlm.nih.gov` | PubMed | **rejected here — see below** |
| `www.koreabiomed.com` | Korea Biomedical Review | newly open — measured below, and rejected |

**Blocked at CONNECT by this session's egress policy** (gateway answers 403;
a property of where the check ran, not of the host). None has ever been
measured, in any sweep:

*Clusters and hospitals* — `www.singhealth.com.sg`, `www.nuhs.edu.sg`,
`www.nhg.com.sg`, `www.sgh.com.sg`, `www.ttsh.com.sg`, `www.ktph.com.sg`,
`www.cgh.com.sg`, `www.kkh.com.sg`, `www.nccs.com.sg`, `www.nhcs.com.sg`,
`www.imh.com.sg`, `www.ncid.sg`

*Agencies and statutory boards* — `www.hsa.gov.sg`, `www.hpb.gov.sg`,
`www.healthhub.sg`, `www.aic.sg`, `www.synapxe.sg`, `www.smc.gov.sg`,
`www.gov.sg`, `data.gov.sg`

*Professional bodies and universities* — `www.sma.org.sg`, `www.smj.org.sg`,
`www.a-star.edu.sg`, `www.nus.edu.sg`, `www.ntu.edu.sg`

*Other Singapore media* — `www.todayonline.com`, `www.businesstimes.com.sg`,
`mothership.sg`, `www.asiaone.com`, `www.asianscientist.com`,
`www.healthxchange.sg`

Two behave differently and are worth distinguishing: `lkcmedicine.ntu.edu.sg`
answers CONNECT with **502**, matching the gateway error recorded in
[status.md](status.md#sources-that-do-not-work) but still through a blocked
path, so it remains unconfirmed; and `smj.org.sg` is the one apex the policy
does allow, but the origin resets the connection, while `www.smj.org.sg` is
refused at CONNECT — the apex/`www` trap that cost CNA a round, in a new place.

The list is long, and the prior on it is poor: these are institutional PR and
consumer-health sites, and the one class of them that has been measured — NUS
Newsroom, Duke-NUS, NUS Medicine, LKC — produced no feed and no health signal.
But "never measured" is not "rejected", and the agencies in particular are a
different register from the schools: HSA is Singapore's drug and device
regulator, and SMC publishes the disciplinary and ethics rulings this
audience's `ethics` section is chronically short of.

**The `www` host is the one to open.** Ten of these apexes are already
allowed and buy nothing, because each answers 301/308 to its `www` host and a
redirect target is a separate CONNECT — `singhealth.com.sg`, `nuhs.edu.sg`,
`sgh.com.sg`, `ncid.sg`, `hsa.gov.sg`, `healthhub.sg`, `a-star.edu.sg`,
`ntu.edu.sg` and `asianscientist.com` all do, and only `nus.edu.sg` serves a
200 directly. This is the trap that cost CNA a round in the third sweep and
`www.koreabiomed.com` two; it is now confirmed to hold across the whole
institutional set.

The ask and the sweep are the same file:
[`config/sg-institutional-hosts.txt`](../config/sg-institutional-hosts.txt),
grouped by what each host is and annotated with the apex/`www` state. Once
they are open, one command measures every one of them:

```bash
python tools/probe_feeds.py --file config/sg-institutional-hosts.txt
```

`tools/probe_feeds.py` is new, and answers the question that comes *before*
`verify_feeds.py`: given a bare hostname, is there anything here to poll? It
tries `<link rel="alternate">` autodiscovery first, then a path list, checks
each find against the host's `robots.txt`, and reports one of four verdicts —
BLOCKED, CHALLENGE, NO FEED, or the feed. **CHALLENGE is a verdict of its own
precisely because of the Duke-NUS mistake below**: a bot-check stub answered
with a 200 is not a page, and reading it as one is how a blocked host came to
be recorded as feed-less.

### Correction: Duke-NUS is not "reachable with no feed"

The [Confirmed dead](#confirmed-dead) table says the Imperva block "has lifted
and there is simply nothing to poll". **That is wrong, and the evidence that
produced it was a challenge page.** Every Duke-NUS path answers with a
212-byte Incapsula stub:

```html
<html><head><META NAME="robots" CONTENT="noindex,nofollow">
<script src="/_Incapsula_Resource?SWJIYLWA=5074a744e2e3d891814e9a2dace20bd4,…">
</script><body></body></html>
```

`/allnews/rss` and `/newshub/rss` return it with a 200, which is what "HTML
with a 200" meant; `/feed` and `/rss.xml` return it with a 404. So Duke-NUS is
**still blocked**, and whether it publishes a feed is unknown rather than
answered. The table has been corrected. This is the same trap `sources/rss.py`
already guards against for feeds — a 200 that is a challenge page — and it is
worth remembering that it catches sweeps too, not just the poller.

`medicine.nus.edu.sg/feed/` is unaffected: it returns a genuine
`text/xml` `wp_die` 500, which is the origin talking. That finding stands.

### PubMed: open, and still not usable

`pubmed.ncbi.nlm.nih.gov` was opened in the second sweep and never tested. It
is the obvious route to Singapore-affiliated research — `Singapore[Affiliation]`
is a precise query in a way a news keyword filter never is. It fails twice
over:

- `robots.txt` carries `Disallow: /rss` **and** `Disallow: /api`. A poller is
  a crawler, which is the reading the fourth sweep settled on for CNA.
- The RSS path needs a server-generated key; `?term=` alone returns the search
  page as HTML, not a feed. The E-utilities host that would sidestep this,
  `eutils.ncbi.nlm.nih.gov`, is refused at CONNECT.

Not retryable without both a different egress and a reading of `robots.txt`
that this project has already declined to make.

### Korea Biomedical Review: measured at last, and rejected

The last open item on the survey. `www.koreabiomed.com` is **open as of
2026-08-27**, and `koreabiomed.com` now redirects into it successfully — so it
could finally be measured, and the answer is a good feed that is nonetheless
the wrong one for this audience.

```
koreabiomed        200      50 2026-08-26   50    300  ok — 2d window, daily poll required
```

Fifty items, RSS 2.0, parses through `sources/rss.py` unchanged, English
throughout, and `robots.txt` disallows only `/admin/`. The register is better
than expected and better than Healthcare Asia's: only 3 of 50 headlines read
as market or deal news, and the window carries pediatric palliative care
missing from 10 of Korea's 16 regions, free flu vaccination widened to age 14,
a septic-shock death and the case for pediatric emergency rooms, and a health
minister's "quiet" reform agenda. That is health policy and health-system
journalism — the register this digest wants.

**Rejected all the same, on 2026-08-27.** Liveness and register were never the
whole bar, and two things decide against it:

- **It is Korea.** The audience is Singaporean and the digest publishes on
  `Asia/Singapore` time. A Korean trade title is a weaker fit than the regional
  Lancet titles already configured, which at least cover South-East Asia and
  the Western Pacific as a region rather than one other country.
- **Volume.** Fifty items across a 2-day window is ~175 a week, which would
  roughly double the store's weekly intake on its own — for a country the
  audience has no particular stake in. Daily polling covers a 2-day window,
  but only just, so it would also be the second-tightest retention in the set.

This is a fit decision rather than a technical one: nothing about the feed is
wrong, and it would work tomorrow if the audience were different. The row stays
in `candidates-asia.yaml`, disabled, as the record — the same treatment CNA and
the Straits Times get, so no later sweep re-litigates it. Reopen only if the
digest ever wants a wider East Asia desk.

**The survey now has no unmeasured candidates, and no open ones.**

---

## Sixth sweep — 2026-08-27, the institutional hosts opened and measured

The fifth sweep named 32 Singapore institutional hosts that had never been
reachable. They were opened the same day, and this is what is behind them.
Run with `tools/probe_feeds.py --file config/sg-institutional-hosts.txt`.

```
8 blocked, 3 challenge, 7 feed, 21 no feed
```

**The prior held, and one host overturned it.** Twenty-one of these serve real
pages and publish no feed at all, which is what every previously-measured
Singapore institution did. But "no feed" was the wrong question to stop on:
MOH publishes no feed either and is a configured source, because `sources/moh.py`
reads its newsroom index out of the page. **HSA does the same thing, on the same
platform, and the existing adapter parses it unchanged.**

### HSA is pollable today, and it is the find of the sweep

`www.hsa.gov.sg/announcements/` is Isomer Next, exactly like MOH's newsroom, and
its flight payload carries 329 dated index records in a byte-identical shape:

```json
{"id":"/announcements/recall-of-carbimazole-5-tablet-5-mg",
 "date":"$D2026-08-26T00:00:00.000Z",
 "plaintextTags":[{"category":"Category","selected":["Product Recalls"]}]}
```

Driving `sources/moh.py` against it — with only `ITEM_BASE` and `RECORD_RE`
repointed, no other change — parses 40 items cleanly:

| Category | Items in the 40-record window |
|---|---|
| Consumer Safety Articles | 14 |
| Product Recalls | 9 |
| Press Releases | 9 |
| Public Consultations | 2 |
| Dear Healthcare Professional Letters | 2 |
| Regulatory Updates | 2 |
| Speeches | 1 |
| Feature Articles | 1 |

A 90-day window at **~3.1 items a week** — twice MOH's steady rate — and the
register is the one this digest is short of. The current window carries a
carbimazole recall, an advisory on ivermectin for unproven clinical uses,
etomidate vaporiser trafficking charges, and Dear Healthcare Professional
letters. This is Singapore's drug and device regulator, and it is the closest
thing to an FDA/MHRA safety stream the set has ever had — Singapore-specific,
which after the fifth sweep only MOH was.

**What it would take.** The adapter is one generalisation away: `ITEM_BASE` and
the `/newsroom/` slug in `RECORD_RE` are the only MOH-specific values in it, and
both are derivable from the source's own `url`. Everything else — the partial
range, the flight-payload parse, the `"])`-inside-a-string trap, the JSON-string
decode, title recasing, the item cap — applies unchanged. Licence needs checking
before it ships: MOH's Terms of Use are restrictive and HSA is a different agency
with its own, so start at `link_only`.

Not done here, because it adds a source rather than measuring one, and that is
a content call rather than a measurement — but unlike `koreabiomed`, which was
turned down on fit, HSA is Singapore's own regulator and the fit is the point.

### Same platform, no use

**HPB** (`/newsroom/`) is Isomer Next with the same index — and effectively dead:
its 40 records span **3,984 days**, about 0.1 items a week, and the newest is
2026-05-21, three months stale at the time of writing. Wrong register too
(brisk-walking campaigns, a wellness app winding down). Not worth a row.

**SMC** (`/publications-and-newsroom/`) is Isomer Next, but its payload carries
**zero** dated records — the `"items"` arrays in it are navigation menus, not an
article index. Its newsroom is built differently and would need its own parser.
That is a shame: SMC publishes the disciplinary and ethics rulings the `ethics`
section is chronically short of, and it was the second-best reason to open this
set. Worth one more look from a browser before it is written off.

**gov.sg** is Isomer Next as well, and is the whole-of-government feed rather
than a health one — the Straits Times failure mode, at government scale.

### Feeds found, and why none earns a row

| Host | Feed | Volume | Verdict |
|---|---|---|---|
| `www.asianscientist.com` | `/feed/` | 30 items, newest 2026-08-26 | Science-wide, not health. Singapore-based, but the same breadth problem as NUS Newsroom |
| `www.businesstimes.com.sg` | `/rss.xml` | 100 items, newest 2026-08-27 | Business news; healthcare only as a sector. Paywalled |
| `mothership.sg` | `/feed/` | 10 items, newest 2026-08-26 | General consumer news |

None of the twelve clusters and hospitals publishes a feed, and none of the
agencies does either. That is now measured rather than assumed.

### Still not reachable, and each for a different reason

The three that did not open are worth separating, because only one is an
allowlist matter:

| Host | State |
|---|---|
| `www.aic.sg` | **Still refused at CONNECT** (403), apex and `www` alike, while DNS resolves fine. Simply missed in the opening — one entry to add |
| `www.smj.org.sg` | **The host refuses us, not the policy.** CONNECT now succeeds; the origin resets the TLS connection immediately after Client Hello, and plain HTTP answers 403. That is the position BMJ is in — a datacenter-address block — and no allowlist change fixes it |
| `lkcmedicine.ntu.edu.sg` | **The hostname has no DNS record at all** — neither A nor AAAA, while `ntu.edu.sg` and `www.ntu.edu.sg` resolve normally. This finally explains the "gateway 502" carried since the first sweep: the gateway cannot resolve it. The school now lives at `www.ntu.edu.sg/medicine`, which serves a 200. Retire the hostname |

### The redirect trap again, in a new form

Five hosts came back BLOCKED despite answering a `3xx` to a plain request,
because the redirect target is a separate CONNECT and none of the targets was
opened. Chasing them turned up a structural change worth recording:

| Requested | Redirects to |
|---|---|
| `www.nhg.com.sg` | `corp.nhg.com.sg` |
| `www.ttsh.com.sg` | `www.nhghealth.com.sg/ttsh` |
| `www.ktph.com.sg` | `www.nhghealth.com.sg/ktph` |
| `www.imh.com.sg` | `www.nhghealth.com.sg/imh` |
| `www.ncid.sg` | `www.nhghealth.com.sg/ncid` |
| `www.todayonline.com` | `www.channelnewsasia.com/today` |

**The National Healthcare Group has consolidated.** Tan Tock Seng, Khoo Teck
Puat, IMH and NCID are no longer separate sites — they are paths under one host.
So the ask shrinks rather than grows: **two hosts, `corp.nhg.com.sg` and
`www.nhghealth.com.sg`, replace five.** Both are refused at CONNECT today.

TODAY needs nothing: it is now a CNA section, and CNA was measured and rejected
in the [third](#third-sweep--2026-08-26-cna-opened-and-measured) and
[fourth](#fourth-sweep--2026-08-27-every-other-route-into-cna) sweeps. Its
`NO FEED` verdict here is that redirect landing on a CNA page.

### One more Incapsula host

`www.nus.edu.sg` answers with the same 212-byte Incapsula stub as Duke-NUS and
`medicine.nus.edu.sg` — a 200 that is not a page. Three NUS hosts, one bot
wall. `news.nus.edu.sg` is the exception and serves its feed normally, which is
why it could be measured and rejected in the second sweep.

---

## Seventh sweep — 2026-08-27, HSA shipped and the adapter generalised

The sixth sweep found HSA behind the institutional hosts and left adding it as
the recommendation. This is what adding it took, and what it turned up.

### The adapter is now the platform's, not MOH's

`sources/moh.py` became `sources/isomer.py`, registered as `isomer_newsroom`.
Only two things in it were ever MOH-specific — the item base and the slug
prefix records hang off — and both come from the source's own `url` now:

```yaml
url: https://www.hsa.gov.sg/announcements/   # → base and /announcements/ prefix
```

So a third Isomer listing is a config row and no code. Everything else — the
byte range, the mid-string cut, the `"])`-inside-a-string trap, the JSON-string
decode, the title recasing — applied to HSA unchanged. `title_case` is a no-op
for it, because HSA does not publish in capitals and the function already left
a non-shouted headline alone.

Three tests exist to keep it that way, and each fails against the hardcoding it
replaced: HSA items linking to `moh.gov.sg`, a listing reading another
listing's records, and a source ignoring its own cap.

### The record cap had to become per-source

`MAX_ITEMS = 40` was sized against MOH's Parliamentary QA bursts and reaches
back 21 days. At HSA's rate the same 40 records reach back **ninety**. That
matters because `window()` selects on when an item was *stored*, so the first
poll makes every record a candidate for that week's issue — the failure the cap
was introduced to prevent, reappearing on a second source.

`max_items` is therefore a source field now. HSA polls 12, measured at 27 days
of history against moh_sg's 40 at 23 — the same margin, arrived at by counting
each agency's own output rather than sharing a number.

### What HSA actually publishes

`tools/isomer_newsroom_probe.py` (renamed with the adapter, and now reading the
row from `sources.yaml` rather than carrying a second copy of it) reports:

```
$ python tools/isomer_newsroom_probe.py hsa_sg
HTTP 206 — 500,001 bytes (asked for 500,000, granted)
HSA Singapore (hsa_sg) — 12 items, capped at 12
  7d   3   Product Recalls 2, Press Releases 1
  30d  12   Product Recalls 5, Press Releases 4, Consumer Safety Articles 1,
            Public Consultations 1, Dear Healthcare Professional Letters 1
the fetched window covers 27 days of history
```

Licence is `link_only`. HSA's Terms of Use (last updated 13 March 2026) permit
the site "for your own personal use" at 1, and 4.3 requires written permission
to reproduce anything beyond what the Copyright Act allows. The same position
as MOH, and the index carries no `description` regardless, so items render as
title and link.

### The scorer could not see it — and that was the scorer's fault

The finding worth recording. Configured and polled for real, **every HSA recall
scored `topic_fit` = 0.00**:

| Item | topic_fit before |
|---|---|
| Recall of Carbimazole 5 Tablet 5 mg | **0.00** |
| Advisory on the Use of Ivermectin for Unproven Clinical Uses | **0.00** |
| Recall of B. Braun Ibuprofen Solution for Infusion 4mg/ml | **0.00** |
| HSA Charges 27-year-old Male for Alleged Trafficking of Etomidate | **0.00** |

Not because they are off-topic — they are the most clinically direct items in
the store — but because **a recall names a product rather than a subject**, and
every theme in `TOPICS` was a subject list. MOH escapes this only because its
headlines are written in policy language: "Reviewing MediSave Withdrawal
Limits" scores 0.85 on `policy` where "Recall of Carbimazole" scores nothing.

So `score.py` gained a `safety` theme at weight 0.80 — under `policy`, over
`treatment`. Pharmacovigilance is interview material in its own right, and the
gap was there before HSA arrived: it was simply invisible while no source
published safety notices.

**It was measured before it was kept, and trimmed twice.** `topic_fit` adds 0.1
of breadth for every theme an item touches, so a list that matches loosely in a
lede lifts every long-text source a little while saying nothing true about it:

| Candidate word | Hits in a 428-item week | Kept? |
|---|---|---|
| `recall*` | 7 — six product recalls, one doctor *recalling* an outbreak | yes |
| `advisory` | 4 — one safety advisory, a Ministerial **Advisory Group**, an advisory committee | **no, 25% precision** |
| `batch*`, `defect*`, `toxicity`, `poisoning` | dropped before measuring — ordinary words in trial abstracts | no |

The result is the point: with `safety` in place the built issue is
**byte-identical** to the build without it. It touches nothing that was already
ranking, and lifts HSA's best item from 148th of 428 to **71st**.

### HSA published nothing this week, and that is worth stating plainly

The sixth sweep rejected CNA partly on rank — its best item was 146th of 431.
HSA's best was 148th of 428 before the `safety` theme, which is close enough
that the comparison has to be made rather than avoided.

They are not the same case, and the difference is not rank:

|  | CNA | HSA |
|---|---|---|
| Items ingested per week | ~150 | ~3 |
| On-topic share | 1 health item in 40 | 12 of 12 |
| Best item, ranked | 146 / 431 | **71** / 428 with `safety` |
| Section it competes for | `also_reading`, always contested | `policy`, `min: 0` and usually empty |

CNA's cost was 150 items a week of general news for nothing. HSA's is twelve
records a poll, all of them health-regulatory. And this week the `policy` slot
went to KFF at 2.680 on merit, then the one-message budget trimmed the section
entirely — so nothing about the week says HSA cannot fill it in another.

That is the honest state: **HSA is configured, verified, and did not appear in
the issue built on the day it shipped.** Worth re-checking after a few weeks —
if it has still published nothing by then, the row should be re-argued rather
than left to sit.

### Also swept

`www.aic.sg` was opened and measured: **no feed**, joining the twenty-two.

NHG has moved again, and the redirect moved with it. `corp.nhg.com.sg` was
opened and `301`s to the **apex** `nhghealth.com.sg` — not to `www`. The fifth
sweep's rule was "the allowlist entry has to name the `www` host"; the accurate
form is **name the host the redirect lands on**, whichever it is.

### NHG is closed, and the 429 was not what it looked like

The apex was opened on request, so all three NHG hosts now reach the origin —
and every one answers **429 on every path**, `/robots.txt` included. The first
reading was that this is a rate limit and the BMJ position, a host gating a
shared egress address. **That was wrong, and the headers say so plainly:**

```
HTTP/2 429
server: Vercel
x-vercel-mitigated: challenge
x-vercel-challenge-token: 2.1787812844.60.…
```

The body is titled *"Vercel Security Checkpoint"*. NHG's new site is on Vercel,
and Vercel answers its bot mitigation with a **429** — so this is a JavaScript
challenge wearing a rate limit's status code. Nothing waits it out, and no
allowlist entry touches it. It is the Duke-NUS mistake in a new costume: there
a bot-check arrived as a `200` and was read as "a page with no feed", here one
arrives as a `429` and reads as "try again later". **Status alone identifies
neither. The body decides.**

`tools/probe_feeds.py` now reads the body behind a 401/403/429 for exactly this
reason, and reports NHG as `CHALLENGE` rather than `BLOCKED` — which matters
because the two call for different actions, and only one of them is "ask for an
allowlist entry".

Verdict: **NHG cannot be polled from a datacenter address**, and nothing on the
allowlist side is outstanding. Twelve hospitals and three clusters are behind
it, so it is the largest single block of Singapore institutional coverage that
stays shut — but it stays shut for the same reason BMJ does, not for one that
can be asked away. Reopen only from a residential or proxied egress.

---

## Recommendation

1. ~~Add `lancet_wpc` and `lancet_sea`~~ — **done.** Both in `sources.yaml` at
   weight 0.9, sections `journals` and `global_health`.
2. ~~Point `conversation_id` at the `kesehatan` feed~~ — **done.** URL changed,
   key kept so stored items keep their source.
3. ~~Add `annals_sg`~~ — **done**, after the second sweep. Weight 1.0, `cc`.
4. ~~Build the MOH adapter~~ — **done.** `sources/moh.py`, ~11 items a week
   overall and ~1.5 of the steady kind, `link_only`, newest 40 records a poll.
5. ~~Get `www.channelnewsasia.com` opened~~ — **done, and CNA is closed.**
   The third sweep measured its feeds: no health feed at any path, 1 health item
   in 40, no higher than 146th of 431 candidates. The fourth sweep then tried
   every other delivery route — the robots-allowed `google-news-feed` and news
   sitemap, scraping the health sections, Drupal JSON:API, per-topic feeds, the
   `cnalifestyle` subdomain, and Google News scoped with `when:7d` — and none
   works. The rows stay in `candidates-asia.yaml`, disabled, as the record.
   Reopen only if CNA ships a health-section feed, which would appear on `/rss`
   and in `robots.txt`.
6. ~~Get `www.koreabiomed.com` opened~~ — **done, measured, and rejected.**
   Opened and measured in the fifth sweep: a good feed, genuinely health-policy
   in register. Turned down on 2026-08-27 all the same — it is Korea rather
   than Singapore, and ~175 items a week would roughly double the store's
   intake for a country this audience has no particular stake in. Row disabled
   in `candidates-asia.yaml` as the record.
7. ~~Decide whether the Singapore institutional hosts are worth opening~~ —
   **done, opened, and measured** in the sixth sweep. 21 of them publish no
   feed, which is what the prior said. But one overturned it:
8. ~~Add HSA~~ — **done**, in the seventh sweep. `sources/moh.py` generalised
   to `sources/isomer.py`, `max_items` became a source field, and `score.py`
   gained a `safety` theme because HSA's recalls scored 0.00 on every existing
   one. Configured at weight 1.0, `link_only`, 12 records a poll. It published
   nothing in the issue built the day it shipped — re-check in a few weeks and
   re-argue the row if that has not changed.
9. **Open `nhghealth.com.sg` — the apex.** `www.aic.sg` was opened and swept
   (no feed). `corp.nhg.com.sg` was opened too and buys nothing, because it
   `301`s to the apex rather than to `www`; `www.nhghealth.com.sg` is open but
   answers 429 persistently. Retire `lkcmedicine.ntu.edu.sg`, which has no DNS
   record at all (the school is at `www.ntu.edu.sg/medicine` now), and
   `www.smj.org.sg`, which the *host* refuses — a TLS reset from a datacenter
   address, the position BMJ is in.

With CNA settled, the Singapore general-news gap is closed as *unfillable*
rather than open: the Straits Times, NUS Newsroom and CNA all failed the same
way — filtering a general feed for health yields almost nothing. Google News
remains the only route to that content, and it needs a URL-resolution step
first.

The config is now at eighteen sources, all verified reachable, with the
regional four carrying Asia and Singapore. The fifth sweep measured what that
buys: 12% of the store is Singapore-published, 18% of a built issue is, and
**MOH is the only source of Singapore-specific health news in the set** —
Annals is Singapore's journal covering the region, not Singapore.

The sixth and seventh sweeps changed that. **HSA is the second such source and
is now configured** — the adapter it needed was already in the tree, and at
~3.1 items a week it roughly triples the Singapore-specific supply on offer.
Whether the ranking lets any of it through is the open question the seventh
sweep leaves.
