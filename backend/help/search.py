"""Deterministic keyword search over the help library.

No index and no embeddings. At this corpus size a ranking pass over the one cached
``Library`` is simpler than anything that has to be built, invalidated or kept in sync,
and "simpler" is what keeps a search feature honest: the same corpus and the same query
always produce the same results in the same order, which is a property a test can pin
(``tests/test_help_library.py``) and a stemmer or a vector store would immediately cost.

Ties break on slug, ascending. That is the whole determinism story — scores collide all
the time on a corpus this small, and without a total order the answer would depend on
dictionary iteration order.

The query argument is model free text, but it only ever SELECTS among our own committed
content: nothing the user typed is echoed back, and every string in a result is drawn
from a topic file. That is why help results are not fenced as untrusted (see
``help.tools``).
"""

import re
from dataclasses import dataclass

from help.library import Library, Topic

MAX_QUERY_CHARS = 200
MAX_QUERY_TOKENS = 12
MAX_RESULTS = 8
DEFAULT_RESULTS = 5
SNIPPET_CHARS = 220

# Weights. Ordered by how much a match in that field says about relevance: a query word
# that IS a slug segment is almost always the topic being asked for; a word appearing
# somewhere in an 8,000-character body says the least.
_W_SLUG = 12
_W_TITLE = 8
_W_ALIAS = 6
_W_HEADING = 3
_W_DESCRIPTION = 2
_W_BODY = 1
_BODY_HITS_CAP = 3          # one long topic must not out-score a precise one on volume
_W_PHRASE = 10              # the whole query appears in a title or alias
# A query word that is a PREFIX of a field word scores half. This is the other half of the
# no-stemmer decision: folding a plural handles "contacts", and a prefix handles the family
# a suffix rule cannot reach — "owns" against "ownership", "score" against "scoring". Both
# are cheap and total; a real stemmer would be a dependency and a source of surprises.
_MIN_PREFIX_CHARS = 3

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _fold(token: str) -> str:
    """Collapse a trailing plural 's'. The entire stemmer, deliberately.

    Without it "who owns this contact" misses a topic whose slug says "contacts", which
    is the single most common way a keyword search over a small corpus feels broken. A
    real stemmer would be a dependency and a source of surprising matches; folding one
    character costs nothing and is applied to BOTH sides, so a word that folds oddly
    ("address" -> "addres") still matches itself.
    """
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def _tokens(text: str) -> list[str]:
    return [_fold(t) for t in _TOKEN_RE.findall(text.lower())]

# Ordinary English glue plus the question words every "how do I …" opens with. Kept
# short on purpose: an aggressive stoplist is how a search stops finding "won deals".
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "is", "it", "for", "on", "with",
    "my", "do", "does", "did", "how", "can", "what", "why", "when", "where", "who",
    "you", "your", "me", "be", "am", "are", "was", "were", "this", "that", "these",
    "those", "from", "at", "by", "as", "if", "not", "no", "but", "so", "there", "here",
    "get", "got", "should", "would", "could", "will", "shall", "have", "has", "had",
    "about", "into", "out", "up", "down", "then", "than", "too", "very", "just",
})


@dataclass(frozen=True)
class Hit:
    topic: Topic
    score: int
    snippet: str


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric words, stopwords and single characters dropped.

    Single characters go because "I" and "a" carry nothing and every apostrophe splits
    one off ("can't" -> "can", "t"). Deduplicated, so repeating a word cannot inflate a
    score, and capped so a pathological query stays bounded.
    """
    seen: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()[:MAX_QUERY_CHARS]):
        if len(raw) < 2 or raw in _STOPWORDS:
            continue
        tok = _fold(raw)
        if tok in seen:
            continue
        seen.append(tok)
        if len(seen) >= MAX_QUERY_TOKENS:
            break
    return seen


def _field_tokens(topic: Topic) -> dict[str, frozenset[str]]:
    return {
        "slug": frozenset(_tokens(topic.slug)),
        "title": frozenset(_tokens(topic.title)),
        "alias": frozenset(t for a in topic.aliases for t in _tokens(a)),
        "heading": frozenset(t for h in topic.headings for t in _tokens(h)),
        "description": frozenset(_tokens(topic.description)),
    }


def score_topic(topic: Topic, tokens: list[str]) -> int:
    """This topic's relevance to an already-tokenized query. Pure; no I/O."""
    fields = _field_tokens(topic)
    body_tokens = _tokens(topic.body)
    weights = (
        ("slug", _W_SLUG), ("title", _W_TITLE), ("alias", _W_ALIAS),
        ("heading", _W_HEADING), ("description", _W_DESCRIPTION),
    )
    score = 0
    for tok in tokens:
        for field, weight in weights:
            if tok in fields[field]:
                score += weight
            elif len(tok) >= _MIN_PREFIX_CHARS and any(
                f.startswith(tok) for f in fields[field]
            ):
                score += weight // 2
        # The body stays exact-match only: it is the weakest signal already, and scanning
        # thousands of words for prefixes would let one long topic win on volume again.
        hits = min(body_tokens.count(tok), _BODY_HITS_CAP)
        score += hits * _W_BODY
    if len(tokens) > 1:
        phrase = " ".join(tokens)
        haystack = " ".join([topic.title, *topic.aliases])
        if phrase in " ".join(_tokens(haystack)):
            score += _W_PHRASE
    return score


def _snippet(topic: Topic, tokens: list[str]) -> str:
    """A short extract from the topic itself — never from the query.

    Picks the paragraph covering the most query words, falling back to the topic's
    description (which is also ours). Headings are stripped so a snippet never opens
    with '##'.
    """
    best, best_cover = "", 0
    for para in topic.body.split("\n\n"):
        cleaned = " ".join(
            line.lstrip("#").strip() for line in para.splitlines() if line.strip()
        ).strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        cover = sum(1 for tok in tokens if tok in lowered)
        if cover > best_cover:
            best, best_cover = cleaned, cover
    text = best if best_cover else topic.description
    if len(text) <= SNIPPET_CHARS:
        return text
    cut = text[:SNIPPET_CHARS]
    # Trim back to a word boundary so a snippet never ends mid-word.
    head, sep, _ = cut.rpartition(" ")
    return f"{head if sep else cut}…"


def search(library: Library, query: str, limit: int = DEFAULT_RESULTS) -> list[Hit]:
    """Topics matching ``query``, best first, ties broken by slug ascending."""
    limit = max(1, min(int(limit), MAX_RESULTS))
    tokens = tokenize(query)
    if not tokens:
        return []
    scored = [
        Hit(topic=t, score=s, snippet=_snippet(t, tokens))
        for t in library.topics
        if (s := score_topic(t, tokens)) > 0
    ]
    scored.sort(key=lambda h: (-h.score, h.topic.slug))
    return scored[:limit]


def nearest(library: Library, query: str, limit: int = 3) -> list[Topic]:
    """Best-guess topics for a slug that did not resolve.

    Always returns something: an unknown topic name fails CLOSED with the corpus's own
    nearest matches rather than a bare miss, and the fallback (first topics in slug
    order) keeps that promise even for a query that scores nothing at all.
    """
    limit = max(1, min(int(limit), MAX_RESULTS))
    # A slug is hyphen-separated, so it tokenizes into exactly the words that should
    # drive the guess — "settings/gmial" still scores "settings" against the folder.
    hits = search(library, query.replace("/", " ").replace("-", " "), limit)
    if hits:
        return [h.topic for h in hits]
    return list(library.topics[:limit])
