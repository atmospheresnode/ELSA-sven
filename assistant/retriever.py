"""RAG-lite: keyword retrieval over the markdown knowledge base.

The corpus is small (a dozen curated chunks), so plain term-frequency scoring
beats the operational cost of an embedding store. Swap this module for a vector
retriever later without touching the prompt builder.
"""
import re
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent / 'knowledge'

_WORD = re.compile(r'[a-z0-9]+')

# Words too common in this domain to carry signal
_STOPWORDS = {
    'the', 'a', 'an', 'is', 'are', 'was', 'of', 'to', 'in', 'on', 'for', 'and',
    'or', 'it', 'this', 'that', 'my', 'i', 'do', 'how', 'what', 'why', 'can',
    'be', 'with', 'vs', 'you', 'me', 'we', 'does', 'not', 'no', 'if',
}


def _tokens(text):
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


_COMMENT = re.compile(r'<!--.*?-->', re.DOTALL)


def _make_chunk(name, text):
    # Strip HTML comments (the "watches:" drift-check declarations) so they
    # never reach the prompt or pollute retrieval tokens.
    text = _COMMENT.sub('', text).strip()
    lines = [l for l in text.splitlines() if l.strip()]
    title = lines[0].lstrip('# ').strip() if lines else name
    return {
        'name': name,
        'title': title,
        'text': text,
        'title_tokens': set(_tokens(title)),
        'body_tokens': set(_tokens(text)),
    }


def _load_chunks():
    return [_make_chunk(path.stem, path.read_text())
            for path in sorted(KNOWLEDGE_DIR.glob('*.md'))]


_CHUNKS = _load_chunks()


def _refresh_release_notes():
    """Fetch and cache the release notes; runs in a background thread."""
    try:
        import requests
        from django.core.cache import cache
        from main.views import parse_release_notes
        resp = requests.get(
            'https://raw.githubusercontent.com/atmospheresnode/ELSA-sven/main/README.md',
            timeout=5)
        resp.raise_for_status()
        releases = parse_release_notes(resp.text)
        # Data kept for a day (stale is better than absent); freshness marker
        # controls how often the background refresh actually runs.
        cache.set('elsa_release_notes', releases, 60 * 60 * 24)
        cache.set('elsa_release_notes_fresh', 1, 60 * 60)
    except Exception:
        pass
    finally:
        try:
            from django.db import connections
            connections.close_all()  # don't leak this thread's DB-cache connection
        except Exception:
            pass


def _release_notes_chunk():
    """Live chunk built from the GitHub README release notes.

    Never blocks the chat request: cached notes are used even when stale, and
    a background thread refreshes them at most once an hour. Returns None
    quietly until the first fetch completes.
    """
    try:
        from django.core.cache import cache
        releases = cache.get('elsa_release_notes')
        if not cache.get('elsa_release_notes_fresh'):
            # Claim the refresh so concurrent requests don't all spawn threads.
            if cache.add('elsa_release_notes_refreshing', 1, 120):
                import threading
                threading.Thread(target=_refresh_release_notes, daemon=True).start()
        if not releases:
            return None
        lines = ["# What's New in ELSA (Latest Release Notes)", '']
        for rel in releases[:4]:
            lines.append(f"Version {rel['version']} ({rel['date']}):")
            for bullet in rel['bullets']:
                lines.append('- ' + re.sub(r'<[^>]+>', '', bullet))
            lines.append('')
        return _make_chunk('release_notes_live', '\n'.join(lines))
    except Exception:
        return None


def _score(chunk, q_tokens):
    """Unique matched terms, title matches weighted: a short chunk that covers
    the query beats a long chunk that merely repeats one query word often."""
    body_hits = len(chunk['body_tokens'] & q_tokens)
    title_hits = len(q_tokens & chunk['title_tokens'])
    return body_hits + 5 * title_hits


def retrieve(query, top_k=3):
    """Return up to top_k knowledge chunks relevant to the query, best first."""
    q_tokens = set(_tokens(query))
    if not q_tokens:
        return []

    chunks = list(_CHUNKS)
    live = _release_notes_chunk()
    if live is not None:
        chunks.append(live)

    scored = []
    for chunk in chunks:
        score = _score(chunk, q_tokens)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda pair: -pair[0])
    return [chunk for _, chunk in scored[:top_k]]
