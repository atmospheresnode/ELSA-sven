# ELSA Assistant (LLM pilot)

A chatbot personal assistant for ELSA users, powered by Google Gemini.
It replaces the floating "Beta Feedback" button in `templates/base-derk.html` with an
"ELSA Assistant" chat widget.

## Capabilities

- Answers questions about using ELSA (bundle building, submission flow, etc.)
- Explains PDS4 archiving concepts, grounded in a curated knowledge base built
  from the PDS4 Information Model v1.24 (1O00)
- Sees the current user's bundles (name/type/status) and the page they're on
- Collects beta feedback conversationally via native function calling and emails
  it to staff (lneakras@nmsu.edu, rupakdey@nmsu.edu)

## Architecture

| Piece | File | Notes |
|---|---|---|
| Views (chat/history/rate) | `views.py` | SSE streaming, persistence, kill switch |
| Provider abstraction | `llm.py` | Model fallback chain, function calling; swap providers here |
| Prompt builder | `prompts.py` | Persona + RAG chunks + user context, injection-hardened |
| Retriever (RAG-lite) | `retriever.py` + `knowledge/*.md` | Keyword scoring over curated chunks |
| Persistence | `models.py` | Conversation + Message (rating, latency, model, errors) |
| Observability | `admin.py` + `assistant` logger | Review transcripts/ratings in Django admin |
| Evals | `evals.json` + `management/commands/assistant_eval.py` | Golden-question regression |

- **Streaming:** replies stream token-by-token (Gemini SSE relayed via
  `StreamingHttpResponse`).
- **Model chain:** free tier allows only ~20 requests/day *per model*, so the
  client falls through `gemini-3.5-flash` → `2.5-flash` → `2.5-flash-lite` →
  `flash-lite-latest` → `2.0-flash`. Override via `GEMINI_MODELS` in settings.
- **Conversations persist server-side.** The widget restores the latest
  conversation when opened; the "+" button starts a new one. History sent to the
  model is capped at 20 messages.
- **Ratings:** thumbs up/down under each assistant reply — review in admin
  (Messages, filter rating = -1) to find weak answers.
- **Rate limits:** 20 messages/min, 200/day per user (Django cache).
- **Kill switch:** set `ASSISTANT_ENABLED = False` in settings to hide the
  widget and disable the endpoints instantly.
- **DB safety:** the view releases its MariaDB connection before the upstream
  LLM call, so a slow model never holds a connection slot.

## Deployment setup

The `elsa/` config directory is gitignored, so manual steps are needed on each
deployment after checking out this branch:

1. **API key** — create a Gemini API key at https://aistudio.google.com/apikey
   and add to `elsa/secrets.py`:

   ```python
   def gemini_api_key():
       return 'YOUR-KEY-HERE'
   ```

2. **Settings** — in `elsa/settings.py`, add `'assistant'` to `INSTALLED_APPS`
   and, below the `from .secrets import *` line:

   ```python
   GEMINI_API_KEY = gemini_api_key()
   ```

3. **URLs** — in `elsa/urls.py`, add to `urlpatterns`:

   ```python
   re_path(r'^assistant/', include('assistant.urls')),
   ```

4. **Migrate** — `python3 manage.py migrate assistant`

5. **Cache table** — the rate limiter needs a cross-process cache. Add to
   `elsa/settings.py`:

   ```python
   CACHES = {
       'default': {
           'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
           'LOCATION': 'elsa_cache_table',
       }
   }
   ```

   then run `python3 manage.py createcachetable`.

## Quota / billing note

The free tier is ~20 requests/day/model (the chain gives ~100/day total). For a
real multi-user beta, enable billing on the Google project — `gemini-2.5-flash`
costs ~$0.30 per million input tokens, i.e. cents/month at ELSA's scale. The
paid tier also excludes prompts from Google's product-improvement (training)
data use, which matters once real scientists use it.

Spend guards: per-user limits (20/min, 200/day) plus a global cap across all
users (default 2000/day; override with `ASSISTANT_GLOBAL_DAILY_CAP` in
settings).

## Operations

```
# Instant kill switch (shared cache; applies to all workers, no restart):
python3 manage.py assistant_toggle off      # or: on / no arg to show state

# Usage + quality report; prints thumbs-down exchanges to triage into
# knowledge updates or eval cases:
python3 manage.py assistant_stats --days 7

# Data retention (dry run by default):
python3 manage.py assistant_purge --days 90 --delete
```

## Keeping knowledge up to date

The assistant's live data (user bundles, completion status, current page, and
the "What's New" release notes chunk fetched from the GitHub README) updates
itself. The conceptual knowledge in `knowledge/*.md` is maintained by hand,
with two guards:

1. Each chunk declares the source files it describes:
   `<!-- watches: build/views.py, templates/build/citation_information -->`.
   Run `python3 manage.py assistant_knowledge_check` before releases (exits
   non-zero when watched code changed after the chunk did).
2. Release checklist rule: any PR changing user-facing behavior also updates
   the matching knowledge chunk, then run the evals below.

## Tests and evals

```
# Unit/integration tests (in-memory SQLite; MariaDB user can't create test DBs)
python3 manage.py test assistant --settings=assistant.test_settings

# Golden-question regression against the live model (consumes quota!)
python3 manage.py assistant_eval --limit 5
```

Run the evals after changing the system prompt, knowledge base, or model chain.
