# ELSA Assistant (LLM pilot)

A chatbot personal assistant for ELSA users, powered by Google Gemini (free tier).
It replaces the floating "Beta Feedback" button in `templates/base-derk.html` with an
"ELSA Assistant" chat widget that can:

- answer questions about using ELSA (bundle building, submission flow, etc.)
- explain PDS4 archiving concepts
- see the current user's bundles and their completion status
- collect beta feedback conversationally and email it to staff
  (same recipients as the old feedback form: lneakras@nmsu.edu, rupakdey@nmsu.edu)

## Deployment setup

The `elsa/` config directory is gitignored, so three manual steps are needed on each
deployment after checking out this branch:

1. **API key** — create a free Gemini API key at https://aistudio.google.com/apikey
   and add to `elsa/secrets.py`:

   ```python
   def gemini_api_key():
       return 'YOUR-KEY-HERE'
   ```

2. **Settings** — in `elsa/settings.py`, add `'assistant'` to `INSTALLED_APPS` and,
   below the `from .secrets import *` line:

   ```python
   GEMINI_API_KEY = gemini_api_key()
   ```

3. **URLs** — in `elsa/urls.py`, add to `urlpatterns`:

   ```python
   re_path(r'^assistant/', include('assistant.urls')),
   ```

No database migrations are required — conversation history lives in the browser tab
and is sent with each request (nothing is persisted server-side).

## Notes

- Model: `gemini-2.5-flash` via the REST `streamGenerateContent` (SSE) endpoint;
  replies stream token-by-token to the widget (see `views.py`).
- Page awareness: the widget sends `window.location.pathname` with each request;
  on a bundle detail page the prompt includes that bundle's name/type/status, so
  "this bundle" resolves correctly.
- Per-user rate limits (Django cache): 10 messages/min, 100/day. Upstream free-tier
  429s are also mapped to a friendly message.
- History is capped server-side at 20 messages / 4,000 chars each per request.
- Markdown rendering in the widget uses `marked` + `DOMPurify` (CDN), with a plain
  escaping fallback if the CDN is unreachable.
- The system prompt (persona, ELSA knowledge, feedback protocol, per-user bundle
  context, current page) is built in `prompts.py`.

## Tests

The MariaDB user can't create test databases, so tests run on in-memory SQLite:

```
python3 manage.py test assistant --settings=assistant.test_settings
```
