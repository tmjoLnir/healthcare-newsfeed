# Status and tracking

The single place to look for **what state this project is in**: what ships, what
is blocked, what was rejected and why, and what is still open. Update this file
when any of those change — the README stays a description of the system, not a
status board.

Last reviewed: **2026-08-27**.

- [Where the project stands](#where-the-project-stands)
- [Source inventory](#source-inventory)
- [Sources that do not work](#sources-that-do-not-work)
- [Open items](#open-items)
- [Shipped](#shipped)
- [Decision log](#decision-log)

---

## Where the project stands

**Complete end to end.** All eighteen sources fetch and persist daily, a week's
candidates cluster, rank and fill the issue's sections, and `newsfeed publish`
renders the issue and sends it to the chat. Preview any week with
`newsfeed publish --dry-run`, which needs no bot token.

What is left is a judgement call rather than a gap — see [Open
items](#open-items).

Two sources are reachable everywhere *except* from a GitHub Actions runner, so
the deployed poller collects sixteen of eighteen until that is sorted. Nothing
fails; `poll` reports them as blocked.

---

## Source inventory

Eighteen sources, all verified reachable on 2026-08-25. Re-check with
`python tools/verify_feeds.py`.

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

| State | Sources |
|---|---|
| **Working from anywhere** | the other sixteen |
| **Blocked from Actions runners** (403, serve normally elsewhere) | NEJM, Annals |
| **Tolerated flaky** | Nature Medicine (`tolerate_failure`, intermittent 500) |

Three of them are the regional signal, added after the survey in
[asia-sources.md](asia-sources.md): Singapore's own journal and the two Lancet
regional titles, joined by The Conversation's Indonesian *health* section in
place of its edition-wide feed. Read that as *regional* rather than Singaporean:
the 2026-08-27 audit found Annals covers South-East Asia and China, and **MOH is
the only source of Singapore-specific health news in the set** — 1 of the 366
items from the other sixteen sources mentioned Singapore at all.

Annals is the one source whose licence is neither CC-BY-ND nor link-only —
CC-BY-NC-SA carries a share-alike term, so it would need downgrading to
`link_only` if the digest were ever monetised.

Five sources need handling that differs from the rest; the reasoning is in
[design.md](design.md#sources-that-need-special-handling).

---

### Sources that do not work

Documented so they are not retried in good faith:

| Source | Why |
|---|---|
| **The BMJ** | Cloudflare returns 429/403 to datacenter IPs; `feeds.bmj.com` fails TLS; *BMJ Opinion* has been dead since January 2022 |
| **Medscape** | Cloudflare bot challenge |
| **NUS Medicine** | WordPress with feeds disabled — 500, `{"code":"wp_die","message":"No feed available."}` |
| **Duke-NUS** | Behind Incapsula on every path — every response is a 212-byte challenge stub, some with a 200. The 2026-08-25 note that the block had lifted was that stub being read as a page; corrected 2026-08-27. Whether a feed exists is unknown |
| **LKC Medicine NTU** | Gateway 502 |

No Singapore *institution* has yielded a feed — three schools, three failure
modes, and Duke-NUS's is a block rather than an answer. Thirty-one further
Singapore institutional hosts have never been reachable from any sweep at all;
they are listed in [asia-sources.md](asia-sources.md). MOH is the exception, and
it is a configured source: it publishes no feed either, but `sources/moh.py`
reads its newsroom index out of the rendered
page ([design.md](design.md#sources-that-need-special-handling)).
[asia-sources.md](asia-sources.md) has the survey the regional sources
came out of, and [`config/candidates-asia.yaml`](../config/candidates-asia.yaml)
re-runs the sweep in one command.

BMJ blocks datacenter IPs outright, and NEJM does the same to GitHub Actions
runners — so expect some publishers to treat any shared egress address this way.
`tools/verify_feeds.py` therefore separates the two cases: a 401/403/429 is
reported as `BLOCKED` and does not fail the sweep, while a 404, an unparseable
response or an empty feed is a real `FAIL`. Pass `--strict` to treat blocks as
failures too. This keeps the CI feed check a genuine gate instead of noise.

---

### Surveyed and rejected on review

Live feeds that were measured and turned down — kept so no future sweep
re-litigates them. Full measurements in [asia-sources.md](asia-sources.md).

| Source | Why not |
|---|---|
| **CNA** (Singapore + Asia) | No health feed exists at any path — seven general feeds, none health. 1 health item in 40, and polling it left the built issue byte-identical (best item 146th of 431 candidates). Every other delivery route was tried in a fourth sweep and none works either — see the decision log |
| **The Straits Times** | No health section feed; keyword-filtering general news yielded ~0 healthcare items. Holds under two days of history. Hard paywalled |
| **NUS Newsroom** | University-wide PR — one of seven sampled items was health-adjacent |
| **Healthcare Asia Magazine** | Investor and market trade press; feed also malformed (`<title>` carries a summary, summaries empty) |
| **Medical Channel Asia** | Consumer wellness rather than medicine. The best feed of its kind found — reconsider only if a consumer-health section is ever wanted |
| **Google News** (SG healthcare query) | Genuinely on-topic, but items link to `news.google.com` redirects rather than publisher URLs, which breaks the store's canonical-URL identity. Would need a URL-resolution step first |

The Singapore general-news gap is now closed as **unfillable** rather than open:
the Straits Times, NUS Newsroom and CNA all fail the same way — filtering a
general feed for health yields almost nothing.

---

## Open items

Judgement calls rather than gaps.

- [ ] **Egress that reaches NEJM and Annals from an Actions runner.** Both serve
      normally elsewhere and both answer the runner with `403`, so they
      contribute nothing to a published issue until the poller has a residential
      or proxied address, or a self-hosted runner. `poll` already classifies
      this as *blocked* rather than *failed*, so it costs nothing else. `poll`'s
      summary line is where to notice it — a source blocked every day is a
      source that is not in the digest, and nothing else will say so.
- [ ] **Whether to add Korea Biomedical Review.** `www.koreabiomed.com` opened
      and was measured on 2026-08-27, closing the survey's last unmeasured
      candidate. It is a good feed — 50 items, RSS 2.0, parses unchanged, and
      health-policy rather than market news in register. Two reasons it was not
      promoted on the spot: it is Korea rather than Singapore, and at ~175 items
      a week it would roughly double the store's intake. A content call, not a
      technical one — [asia-sources.md, fifth sweep](asia-sources.md).
- [ ] **Whether to ask for the Singapore institutional hosts.** 31 of them are
      still refused at CONNECT and have never been measured in any sweep — the
      three clusters, the hospitals, HSA, HPB, HealthHub, SMA, SMJ, A\*STAR,
      `data.gov.sg`. The prior is poor, since every Singapore institution that
      has been measured turned out to publish no feed, so this is worth asking
      for only alongside a consumer-health or institutional-research section.

---

## Shipped

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
      chosen in [persistent-store.md](persistent-store.md)
- [x] An MOH adapter — no feed exists, so `sources/moh.py` reads the newsroom
      index out of the page's Next.js payload, capped at the newest 40 records
- [x] Singapore and Asia coverage decided and shipped — Annals plus the two
      Lancet regional titles, and The Conversation Indonesia moved to its
      health section. Surveyed, measured and costed in
      [asia-sources.md](asia-sources.md), including what was
      rejected and why
- [x] CNA evaluated and rejected — the last regional candidate that
      needed measuring; see the decision log below
- [x] Singapore coverage audited against the live corpus, and every candidate
      on the regional survey now measured — including `koreabiomed`, the last
      one outstanding. The audit fixed a WordPress footer that was being stored
      as body text and corrected the Duke-NUS entry above

---

## Decision log

Dated decisions, newest first. Each links to where the reasoning lives.

| Date | Decision |
|---|---|
| 2026-08-27 | **Singapore coverage audited end to end, and the last candidate measured.** All 18 sources polled (416 items): 50 are Singapore-published, and exactly 1 of the other 366 mentions Singapore at all. Annals is Singapore's journal covering the *region*, not Singapore — 7 of its 10 items match "Singapore" only in a WordPress footer — so **MOH is the digest's only source of Singapore-specific health news**. Fixed a real defect the audit surfaced: that footer was being stored as body text, and for Annals' shortest item it *was* the whole rendered extract. `www.koreabiomed.com` opened and was measured; 31 Singapore institutional hosts remain refused at CONNECT and unmeasured; the Duke-NUS "reachable, no feed" note was corrected to "still Incapsula-blocked" — [asia-sources.md, fifth sweep](asia-sources.md) |
| 2026-08-27 | **CNA closed for good.** A fourth sweep tried every remaining delivery route: the robots-allowed `/api/v1/google-news-feed` (full text, but a 9.3-hour window at ~129 items/day and no category filter), the news sitemap, scraping the health sections (no dates at all, and evergreen rather than current), Drupal JSON:API (403), per-topic feeds (404), the `cnalifestyle` subdomain (blocked), and Google News with `when:7d` (68% radio and TV segments, and no publisher URL anywhere). The blocker is upstream of delivery: CNA barely publishes health journalism — [asia-sources.md, fourth sweep](asia-sources.md) |
| 2026-08-26 | **CNA rejected.** `www.channelnewsasia.com` was opened and CNA measured end to end: no health feed at any path, 1 health item in 40, zero effect on the built issue. Rows kept disabled in `candidates-asia.yaml` as the record — [asia-sources.md, third sweep](asia-sources.md) |
| 2026-08-25 | **Annals added** after the second regional sweep — Singapore's own peer-reviewed journal, CC-BY-NC-SA, full text in-feed. Found to be blocked from Actions runners after it shipped |
| 2026-08-25 | **MOH adapter built** — no feed exists, so `sources/moh.py` reads the newsroom index out of the page's Next.js payload, capped at the newest 40 records — [design.md](design.md#sources-that-need-special-handling) |
| 2026-08-25 | **Lancet WPC and SEA added**, and The Conversation Indonesia repointed at its `kesehatan` health section — [asia-sources.md](asia-sources.md) |
| 2026-08-25 | **Store moved out of the runner** into a GitHub Release asset — [persistent-store.md](persistent-store.md) |
