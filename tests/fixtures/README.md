# Feed fixtures

Captured responses, so the suite runs offline and stays stable when a
publisher changes its output. Keep at least one fixture per format: the
configured set spans RSS 1.0 (RDF), RSS 2.0, Atom and OData JSON.

| File | Source | Format | Captured | Trimmed to |
|---|---|---|---|---|
| `bbc_health_rss2.xml` | `feeds.bbci.co.uk/news/health/rss.xml` | RSS 2.0 | 2026-08-25 | 4 of 52 items |
| `kff_rss2.xml` | `kffhealthnews.org/feed/` | RSS 2.0 + `content:encoded` | 2026-08-25 | 2 of 10 items |
| `nature_med_rss1.xml` | `nature.com/nm.rss` | RSS 1.0 (RDF) | 2026-08-25 | 3 of 8 items |
| `conversation_uk_atom.xml` | `theconversation.com/uk/health/articles.atom` | Atom 1.0 | 2026-08-25 | 3 of 25 entries |
| `annals_sg_rss2.xml` | `annals.edu.sg/feed/` | RSS 2.0 (WordPress) | 2026-08-27 | 3 of 10 items |
| `hsa_announcements_partial.html` | `hsa.gov.sg/announcements/` | Isomer Next (RSC) | 2026-08-27 | 6 of 12 records |
| `who_news_odata.json` | `who.int/api/news/newsitems` | OData JSON | 2026-08-25 | 3 of 20 records |
| `who_dons_odata.json` | `who.int/api/news/diseaseoutbreaknews` | OData JSON | 2026-08-25 | 2 of 20 records |

The feeds were trimmed by deleting trailing entry elements only — except
`annals_sg_rss2.xml`, where the three were *chosen* rather than truncated to,
because the property under test sits seventh in a ten-item window. Everything
else is byte-for-byte as served. The two OData captures were truncated by
slicing the `value` array and re-serialising, so their whitespace differs from
the wire — no field was altered or removed. Five deliberate properties worth
preserving when refreshing them:

* `nature_med_rss1.xml` keeps its full `<items><rdf:Seq>` block — eight
  `rdf:li` references against three actual `<item>` elements. That ordering
  block is metadata, and the RSS 1.0 sources are the reason `rss.py` says so;
  the mismatch is what makes `test_rdf_seq_block_is_not_an_entry` meaningful.
* `bbc_health_rss2.xml` keeps the `?at_medium=RSS&at_campaign=rss` tracking
  parameters BBC appends to feed links. Stripping them is `canonical_url`'s
  job, not the adapter's.
* The two WHO captures are a matched pair, and the point is that they do *not*
  agree: news records have no `Summary` field at all and carry `NewsType`,
  while outbreak news carries a plain-text `Summary`, title overrides, and
  HTML body sections the adapter ignores. `who_dons_odata.json` is large
  because those sections are large; keep them, since leaving them out would
  stop the fixture proving they are ignored.

* `annals_sg_rss2.xml` keeps one full-text research article against two
  PDF-only stubs, and every one of the three ends in WordPress's
  `The post … appeared first on Annals Singapore.` footer. That footer is the
  point: on `Continuing Medical Education` the body is 178 characters against
  the `journals` section's 180-character budget, so before `rss.py` stripped it
  the rendered extract was boilerplate end to end. Keep a short item and a long
  one when refreshing — the long one is what proves the strip is anchored at
  the end and does not eat the article.

* `hsa_announcements_partial.html` is `moh_newsroom_partial.html`'s pair, and
  the reason both are kept: the adapter is shared, and nothing in it is allowed
  to be agency-specific. It is the index chunk of a byte-ranged response —
  `self.__next_f.push([1,"…` opened and never closed — holding six whole
  records and a seventh cut mid-object, under `/announcements/` rather than
  `/newsroom/`. Rebuild it the same way as MOH's, from a live
  `Range: bytes=0-500000` fetch of `https://www.hsa.gov.sg/announcements/`.

* `moh_newsroom_partial.html` is the odd one out: a trimmed capture rather
  than a whole response, because the whole response is 7.5 MB. It keeps the
  first six index records verbatim, cuts the seventh mid-object, and never
  closes its `self.__next_f.push([1,"…` chunk — which is exactly what a
  byte-ranged fetch of MOH looks like, and the property the adapter is built
  around. It also opens with a chunk containing a literal `"])` inside a
  string, so a naive split on the terminator would silently drop every record
  after it. Rebuild it from a live `Range: bytes=0-500000` response: keep the
  `self.__next_f.push([1,"` chunk that contains `\"items\":[`, cut it a couple
  of hundred characters into the seventh record, and drop the closing `"])`.

Refresh by re-fetching the URL above with the browser User-Agent from
`tools/verify_feeds.py`, then dropping the trailing entries. The WHO endpoints
need `?$orderby=PublicationDateAndTime%20desc&$top=20`; without it they return
an arbitrary page, and a capture taken that way would be worse than useless.
