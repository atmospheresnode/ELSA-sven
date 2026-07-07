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


def _load_chunks():
    chunks = []
    for path in sorted(KNOWLEDGE_DIR.glob('*.md')):
        text = path.read_text()
        title = text.splitlines()[0].lstrip('# ').strip() if text else path.stem
        chunks.append({
            'name': path.stem,
            'title': title,
            'text': text,
            'title_tokens': set(_tokens(title)),
            'body_tokens': _tokens(text),
        })
    return chunks


_CHUNKS = _load_chunks()


def retrieve(query, top_k=3):
    """Return up to top_k knowledge chunks relevant to the query, best first."""
    q_tokens = set(_tokens(query))
    if not q_tokens:
        return []

    scored = []
    for chunk in _CHUNKS:
        body_hits = sum(1 for t in chunk['body_tokens'] if t in q_tokens)
        title_hits = len(q_tokens & chunk['title_tokens'])
        score = body_hits + 5 * title_hits
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda pair: -pair[0])
    return [chunk for _, chunk in scored[:top_k]]
