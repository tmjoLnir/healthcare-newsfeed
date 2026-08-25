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

Each was trimmed by deleting trailing entry elements only; everything else
is byte-for-byte as served. Two deliberate properties worth preserving when
refreshing them:

* `nature_med_rss1.xml` keeps its full `<items><rdf:Seq>` block — eight
  `rdf:li` references against three actual `<item>` elements. That ordering
  block is metadata, and the RSS 1.0 sources are the reason `rss.py` says so;
  the mismatch is what makes `test_rdf_seq_block_is_not_an_entry` meaningful.
* `bbc_health_rss2.xml` keeps the `?at_medium=RSS&at_campaign=rss` tracking
  parameters BBC appends to feed links. Stripping them is `canonical_url`'s
  job, not the adapter's.

Refresh by re-fetching the URL above with the browser User-Agent from
`tools/verify_feeds.py`, then dropping the trailing entries.
