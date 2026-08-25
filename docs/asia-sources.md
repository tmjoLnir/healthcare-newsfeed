# Singapore and Asian healthcare sources

Measured 2026-08-25 against the live web, using `tools/verify_feeds.py`'s
checks and — for the three candidates worth adding — the project's own
`sources/rss.py` adapter end to end.

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

The README records MOH as *"No RSS — the site runs on Isomer; every feed path
404s"*. Both halves are still true — `/rss`, `/feed.xml` and `/newsroom/rss.xml`
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
`tools/moh_newsroom_probe.py` reports which of the two actually happened.

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

1. `GET https://www.moh.gov.sg/newsroom/` with `Range: bytes=0-500000`. The
   final `self.__next_f.push([1,"…"])` chunk is cut mid-string, so the parser
   ends on the last complete unit instead of anchoring on `"])` — and rejects a
   `"])` that turns out to be an escaped quote inside a string, which would
   otherwise silently drop every record after it.
2. Decode each chunk **as a JSON string, not with `unicode_escape`**. That was
   the one real trap: `unicode_escape` round-trips through latin-1 and turns
   every multi-byte character into mojibake — 2,210 corrupted bytes on the live
   page. The first version of `tools/moh_newsroom_probe.py` had this bug; the
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
so the daily poll the README defends would silently drop items — this would be
the only source needing a faster cadence. And ST is hard-paywalled, so items
render as headline and link anyway.

Verdict: high volume, near-zero healthcare signal, worst-in-set retention. Not
worth the row.

---

## Confirmed dead

Checked directly; do not retry without re-testing.

| Candidate | Result |
|---|---|
| `moh.gov.sg/rss`, `/feed.xml`, `/newsroom/rss.xml` | 404 — no feed anywhere (use the newsroom index above) |
| **Duke-NUS** | Host now reachable — but no feed exists. `/feed`, `/allnews/rss`, `/newshub/rss` all return HTML with a 200; `/rss.xml`, `/sitemap.xml` 404. Supersedes the earlier "Imperva/Incapsula" note: the block has lifted and there is simply nothing to poll |
| **NUS Medicine** | `medicine.nus.edu.sg/feed/` → 500, `{"code":"wp_die","message":"No feed available."}` — WordPress with feeds disabled |
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

`lkcmedicine.ntu.edu.sg` returned a gateway 502, matching the README's existing
note — but through the same blocked path, so it is not a fresh confirmation.

The highest-value ones to check first are **CNA** (the only Singapore
general-news outlet with documented per-section RSS, including a mental-health
section, and unpaywalled), **Korea Biomedical Review** (English-language
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

---

## Recommendation

1. ~~Add `lancet_wpc` and `lancet_sea`~~ — **done.** Both in `sources.yaml` at
   weight 0.9, sections `journals` and `global_health`.
2. ~~Point `conversation_id` at the `kesehatan` feed~~ — **done.** URL changed,
   key kept so stored items keep their source.
3. ~~Add `annals_sg`~~ — **done**, after the second sweep. Weight 1.0, `cc`.
4. ~~Build the MOH adapter~~ — **done.** `sources/moh.py`, ~11 items a week
   overall and ~1.5 of the steady kind, `link_only`, newest 40 records a poll.
5. **Get `www.channelnewsasia.com` and `www.koreabiomed.com` opened**, then
   re-run `config/candidates-asia.yaml`. CNA is the one gap neither sweep
   filled, and the only outstanding item on this survey.

The config is now at eighteen sources, all verified reachable, with the
regional four carrying Asia and Singapore.
