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
        'body_tokens': _tokens(text),
    }


def _load_chunks():
    return [_make_chunk(path.stem, path.read_text())
            for path in sorted(KNOWLEDGE_DIR.glob('*.md'))]


_CHUNKS = _load_chunks()


def _release_notes_chunk():
    """Live chunk built from the GitHub README release notes.

    Reuses the About page's fetcher and its 1-hour cache, so "what's new"
    answers track the latest release automatically with no hand-edited
    knowledge. Returns None quietly when the notes can't be fetched.
    """
    try:
        from django.core.cache import cache
        releases = cache.get('elsa_release_notes')
        if releases is None:
            import requests
            from main.views import parse_release_notes
            resp = requests.get(
                'https://raw.githubusercontent.com/atmospheresnode/ELSA-sven/main/README.md',
                timeout=5)
            resp.raise_for_status()
            releases = parse_release_notes(resp.text)
            cache.set('elsa_release_notes', releases, 60 * 60)
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
        body_hits = sum(1 for t in chunk['body_tokens'] if t in q_tokens)
        title_hits = len(q_tokens & chunk['title_tokens'])
        score = body_hits + 5 * title_hits
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda pair: -pair[0])
    return [chunk for _, chunk in scored[:top_k]]
