"""Render a Digest into Telegram-ready messages.

Constraints:
  * 4096 characters per message, so a full issue is split across an ordered
    burst — split on section boundaries, never mid-item
  * licence-aware bodies: PUBLIC_DOMAIN and CC_REPUBLISHABLE items may carry
    a long extract; LINK_ONLY items get headline + own-words summary + link
  * paywalled sources are labelled inline, so readers know before they click
  * NEJM supplies no usable summary (its feed carries an 87-character
    citation string), so those items render title + link only

Two of those are one rule between them. **Licence sets a ceiling and the
section's style sets the ask**, and every extract is the smaller of the two:
a `long` story from STAT is capped to a quotation because STAT is link-only,
while a `long` one from WHO is not. Putting the ceiling in one table beside
the styles is what makes the republishing rule in the README checkable
rather than a convention someone has to remember.

The NEJM case is likewise not a special case. The RSS 1.0 journals all open
their description with a citation — Nature Medicine ships "Nature Medicine,
Published online: 24 August 2026; doi:10.1038/…" and then the abstract —
and printing that to a reader would look broken wherever it appeared. So
the citation is stripped and whatever follows is the summary. NEJM's feed
happens to be citation and nothing else, so nothing is left and the item
falls back to title + link, which is exactly the intended outcome.

Licence and paywall status live on `Source` rather than `Item`, so this
takes the configured sources alongside the digest — the stub's one-argument
signature could not reach the constraints in its own docstring.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass

from ..models import Digest, Item, Licence, Source
from ..telegram import length
from .template import IssueSpec, SectionSpec

# The most body text each licence permits, in characters, whatever the
# section asked for. LINK_ONLY is a short quotation for orientation, not a
# republication: the README's rule is a headline, a summary in your own
# words and a link, and nothing here can write the second of those.
LICENCE_CEILINGS = {
    Licence.PUBLIC_DOMAIN: 1200,
    Licence.CC_REPUBLISHABLE: 1200,
    Licence.LINK_ONLY: 200,
}

# Below this, a summary is a few words and an ellipsis — worse than none.
MIN_SUMMARY = 60

# A headline long enough to fill a message is a broken feed, not a headline.
MAX_TITLE = 200

PAYWALL_MARK = "🔒 paywalled"
CONTINUED = "(cont.)"
BULLET = "•"


class RenderError(ValueError):
    """A digest cannot be rendered — a section the template no longer has."""


# --- publisher boilerplate --------------------------------------------------

# Every publisher puts something in front of the text it means as a summary,
# and each puts something different there. Two shapes cover the configured
# set: the journals lead with a citation, and The Conversation leads with the
# credit line from the article's hero image. Both are removed here rather
# than in the adapter, whose rule is that a RawItem is the item as found —
# stripping upstream would destroy evidence the pipeline scores on.

# What a journal feed puts where the description belongs. Strong markers are
# citation and nothing else; weak ones are ordinary words that happen to
# precede a number, and one alone proves nothing — "a policy issue 5 years
# in the making" is a sentence, not a volume reference. So a preamble counts
# as a citation on one strong marker or two weak ones, and a summary that
# earns neither is returned untouched.
_STRONG_MARKER = re.compile(
    r"(?i)\bpublished\s+(?:online|ahead\s+of\s+print)\b"
    r"|\bdoi:\s*\S+"
    r"|\b\d{4};\s*\d+\s*\(\d+\)"          # 2026;395(8)
    r"|\b\d+\s*\(\d+\)\s*:\s*\d+"         # 395(8):723
)

_WEAK_MARKER = re.compile(
    r"(?i)\b(?:volume|issue|page|pages|vol\.|no\.|pp?\.)\s*\d+"
)

# A citation is a preamble and a short one — journal name, then the fields.
# A doi quoted in the body of an abstract is not evidence that the abstract
# was a citation, so matches beyond this point do not count.
_CITATION_SCAN = 250

_MONTHS = frozenset({
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
})

# Words that are still the citation rather than the start of the summary.
_CITATION_WORDS = _MONTHS | {"published", "online", "print", "ahead", "of", "epub", "suppl"}

_WORD = re.compile(r"[A-Za-z]+")


def strip_citation(summary: str) -> str:
    """Drop a leading journal citation, returning the summary that follows.

    Nature Medicine, The Lancet and JAMA all prefix the abstract with one;
    NEJM's feed carries the citation alone, so this returns "" for it and
    the item renders title + link. A summary with no citation is returned
    untouched, which is every non-journal source in the configured set.
    """
    text = (summary or "").strip()
    head = text[:_CITATION_SCAN]
    markers = sorted(_STRONG_MARKER.finditer(head), key=lambda m: m.end())
    weak = sorted(_WEAK_MARKER.finditer(head), key=lambda m: m.end())
    if not markers and len(weak) < 2:
        return text

    last = max(markers + weak, key=lambda m: m.end())
    return text[_end_of_citation(text, last.end()):].lstrip(" .,;:-–—")


def _end_of_citation(text: str, position: int) -> int:
    """Walk past the citation's trailing fields to the first real word.

    NEJM's runs "…Page 723-733, August 2026." past its last marker and ends;
    Nature's ends at the doi and the abstract starts immediately. Consuming
    punctuation, numbers and month names — and stopping at anything else —
    handles both without needing to know which source it is reading.
    """
    while position < len(text):
        word = _WORD.search(text, position)
        if word is None:
            return len(text)                 # nothing but citation fields left
        if word.group(0).lower() not in _CITATION_WORDS:
            return word.start()
        position = word.end()
    return position


# A photo credit reads "Photographer/Agency", and it is the separator that
# makes it safe to match: a bare agency name would fire on an article about
# Getty or a wire report attributed to Reuters, while "/Getty" and
# "lev radin/ Shutterstock" are only ever credit lines.
_IMAGE_CREDIT = re.compile(
    r"(?i)(?:/|©|\bphotos?\s*:|\bcredit\s*:|\bimage\s*:)\s*"
    r"(?:shutterstock|getty(?:\s+images)?|alamy|unsplash|pexels|pixabay"
    r"|istock(?:photo)?|adobe\s+stock|wikimedia(?:\s+commons)?|ap\s+photo"
    r"|reuters|afp|epa)(?:\.com)?"
)

# The credit sits at the top of the article, above the first paragraph. A
# later one belongs to an inline figure, and the text around it is the
# summary rather than boilerplate.
_CREDIT_SCAN = 300


def strip_image_credit(summary: str) -> str:
    """Drop a leading photo caption and credit, returning the article text.

    The Conversation ships whole articles, and every one of them opens with
    its hero image's caption — so the explainer section, which carries the
    most text of any block, would otherwise open with "New Africa/
    Shutterstock.com" before reaching a word of the piece.
    """
    match = _IMAGE_CREDIT.search(summary[:_CREDIT_SCAN])
    return summary[match.end():].lstrip(" .,;:-–—") if match else summary


def summary_text(summary: str) -> str:
    """An item's summary with its publisher's boilerplate removed."""
    return strip_image_credit(strip_citation(summary))


# --- text -------------------------------------------------------------------

def escape(text: str) -> str:
    """Escape for Telegram's HTML parse mode, in body text and hrefs alike."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def trim(text: str, limit: int) -> str:
    """Cut to `limit` characters on a word boundary, marking the cut.

    The ellipsis is inside the limit, not added to it — the caller is
    budgeting against a hard message cap.
    """
    text = text.strip()
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return cut.rstrip(" ,;:.—–-") + "…"


def fit(text: str, room: int) -> str:
    """Trim, escape, and keep shrinking until the *escaped* form fits.

    Escaping expands — one `&` becomes five characters — so a budget spent
    on the plain text can overrun once the entities are in. Everything here
    is budgeting against a hard message cap, so the check has to be made on
    what actually goes out.
    """
    budget = room
    while budget > 0:
        escaped = escape(trim(text, budget))
        if length(escaped) <= room:
            return escaped
        budget -= length(escaped) - room
    return ""


def _link(item: Item, room: int) -> str:
    href = escape(item.canonical_url)
    budget = min(MAX_TITLE, max(0, room - length(href) - 15))    # <a href="">…</a>
    title = fit(item.title, budget)
    return f'<a href="{href}">{title}</a>' if title else ""


def _attribution(source: Source | None, key: str) -> str:
    name = escape(source.name if source else key)
    return f"{name} · {PAYWALL_MARK}" if source and source.paywalled else name


# --- blocks -----------------------------------------------------------------

def _budget(source: Source | None, spec: SectionSpec, room: int) -> int:
    """How much summary this item may carry: the ask, the ceiling, the room."""
    licence = source.licence if source else Licence.LINK_ONLY
    return min(spec.body_chars, LICENCE_CEILINGS[licence], room)


def item_block(item: Item, source: Source | None, spec: SectionSpec, limit: int) -> str:
    """One item, rendered for its section's style.

    Built to fit `limit` by construction rather than truncated afterwards:
    cutting assembled HTML could land inside a tag or an entity, and
    Telegram rejects the whole message rather than the broken span.
    """
    block = _styled(item, source, spec, limit)
    if length(block) <= limit:
        return block
    # No composed form fits — a headline long enough to fill a message, or a
    # message limit small enough to make one. Plain text has no markup to break.
    return fit(item.title, limit)


def _styled(item: Item, source: Source | None, spec: SectionSpec, limit: int) -> str:
    attribution = _attribution(source, item.source_key)

    if spec.style == "headline":
        link = _link(item, limit - length(attribution) - 10)
        block = f"{BULLET} {link} — <i>{attribution}</i>"
        return block if length(block) <= limit else f"{BULLET} {_link(item, limit - 2)}"

    link = _link(item, limit - length(attribution) - 20)
    head = f"<b>{link}</b>"
    tail = f"<i>{attribution}</i>"
    room = limit - length(head) - length(tail) - 2               # two newlines
    body = fit(summary_text(item.summary), _budget(source, spec, room))
    if length(body) < MIN_SUMMARY:
        body = ""                                               # see MIN_SUMMARY
    return "\n".join(part for part in (head, body, tail) if part)


def _header(digest: Digest, spec: IssueSpec, limit: int) -> str:
    """The issue's masthead, itself budgeted — it opens the first message."""
    dates = f"<i>{escape(_dates(digest, spec))}</i>"
    suffix = f"</b> — Issue {digest.issue}"
    title = fit(spec.title, limit - length(dates) - length(suffix) - 4)
    return f"<b>{title}{suffix}\n{dates}"


def _dates(digest: Digest, spec: IssueSpec) -> str:
    """The week the issue covers, in the issue's own timezone.

    week_end is exclusive — cli.week_bounds sets it a second past the last
    moment included — so the last day is a second back from it.
    """
    start = digest.week_start.astimezone(spec.tz)
    end = (digest.week_end - dt.timedelta(seconds=1)).astimezone(spec.tz)
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}–{end.day} {end:%B %Y}"
    if start.year == end.year:
        return f"{start.day} {start:%B} – {end.day} {end:%B %Y}"
    return f"{start.day} {start:%B %Y} – {end.day} {end:%B %Y}"


# --- assembly ---------------------------------------------------------------

@dataclass(frozen=True)
class _Block:
    """One section's rendered parts, ready for the packer."""

    heading: str
    separator: str
    items: list[str]


def render(digest: Digest, sources: Mapping[str, Source], spec: IssueSpec) -> list[str]:
    """Return the ordered message bodies for one issue."""
    limit = spec.max_message_chars
    sections: list[_Block] = []

    for section in digest.sections:
        section_spec = spec.section(section.key)
        if section_spec is None:
            raise RenderError(
                f"digest carries section '{section.key}', which the template "
                f"no longer defines — rebuild the issue against this template"
            )
        heading = f"<b>{escape(section.heading)}</b>"
        # Every item is built to fit alongside its heading, so a section
        # that spills can always open the next message with both. Budget for
        # the continuation form, which is the longer of the two — reserving
        # only the bare heading overruns the cap on exactly the messages
        # that were already too full.
        room = limit - length(heading) - length(CONTINUED) - 3
        sections.append(_Block(
            heading=heading,
            # A bulleted list of headlines reads as a list; the longer styles
            # are paragraphs and need the air between them.
            separator="\n" if section_spec.style == "headline" else "\n\n",
            items=[item_block(item, sources.get(item.source_key), section_spec, room)
                   for item in section.items],
        ))

    return _pack(_header(digest, spec, limit), sections, limit)


def _pack(header: str, sections: list[_Block], limit: int) -> list[str]:
    """Fill messages in order, breaking between items and never inside one.

    A section that does not fit what is left of a message moves whole to the
    next one; a section too long for any single message is split across
    several, repeating its heading so a reader landing mid-block can still
    see which section they are in.
    """
    messages: list[str] = []
    current = header
    for section in sections:
        pending = section.heading            # unwritten until an item joins it
        for block in section.items:
            joint = "\n\n" if pending else section.separator
            addition = (f"\n\n{pending}" if pending else "") + f"{joint}{block}"
            if length(current) + length(addition) <= limit:
                current += addition
                pending = None
                continue

            messages.append(current)
            carried = section.heading if pending else f"{section.heading} {CONTINUED}"
            current = f"{carried}\n\n{block}"
            pending = None

    messages.append(current)
    return [message for message in messages if message.strip()]
