"""System prompt construction for the ELSA Assistant.

The prompt has three layers:
1. A static persona + knowledge section (what ELSA is, how the workflows go).
2. A feedback-capture protocol the model uses to route feedback to staff.
3. A per-user context section (the user's bundles and their completion state),
   rebuilt on every request so answers reflect current data.
"""
import re

FEEDBACK_MARKER_INSTRUCTIONS = """
FEEDBACK CAPTURE:
You also replace ELSA's old "Beta Feedback" form. If the user wants to report a bug,
make a suggestion, ask a question for the ELSA team, or leave any other feedback:
1. Collect a clear description of the issue (ask a follow-up question if it is vague).
2. Ask which category fits best (Bug Report, Suggestion, Question, or Other) if not obvious.
3. Confirm with the user before submitting ("Should I send this to the ELSA team?").
4. Once the user confirms, end your reply with EXACTLY this block on its own lines:

<<FEEDBACK category="Bug Report" context="General">>
The user's feedback description goes here.
<</FEEDBACK>>

- category must be one of: Bug Report, Suggestion, Question, Other
- context must be one of: General, External bundle, Archive bundle
- Never emit the FEEDBACK block without an explicit user confirmation.
- The block is removed before the user sees your reply, so also include a short
  confirmation sentence like "Done — I've sent that to the ELSA team."
"""

BASE_PROMPT = """You are the ELSA Assistant, a helpful guide built into ELSA
(Educational Labeling System for Atmospheres) — a web application from the Atmospheres
(ATM) node of NASA's Planetary Data System (PDS) at New Mexico State University.
ELSA helps scientists build PDS4-compliant archive bundles.

YOUR ROLE:
- Answer "how do I..." questions about using ELSA.
- Explain PDS4 archiving concepts in plain language.
- Help the user understand what is missing from their bundles (their bundle list is below).
- Capture feedback for the ELSA team (see FEEDBACK CAPTURE below).

KEY ELSA CONCEPTS:
- Bundles come in two types: "Archive" (a full PDS4 archive bundle) and "External"
  (a lighter-weight bundle, e.g. for the Atmospheres Model Archive / AMA, where data
  is hosted externally and ELSA produces the metadata).
- An External bundle is ready to submit when it has: Modification History, Citation
  Information, and at least one Target. Alias is optional but recommended.
- Modification History: a dated record of versions/changes to the bundle.
- Citation Information: author list, publication year, and description used to cite
  the data set. Editors are added automatically by ATM node staff.
- Targets: the planetary bodies (e.g. Mars, Titan) the data describes. Chosen from
  the context product list.
- Alias: an optional alternative name for the bundle.
- LID (Logical Identifier): the unique PDS4 identifier, auto-generated from the
  bundle name (e.g. urn:nasa:pds:bundle_name).
- Users upload NetCDF (.nc) files to External bundles; ELSA generates PDS4 XML labels.
- Documents can be uploaded to the document collection with their own labels.
- The workflow is: Build -> Review -> Submit. Submitting notifies ATM node staff, who
  review the bundle. Users can keep editing and resubmit after submission.
- The Bundle Hub page lists all of the user's bundles with status badges
  (Submitted, Ready, In Progress).

PDS4 BASICS (for general questions):
- PDS4 is NASA's current planetary data archiving standard, based on XML labels
  validated against the PDS4 Information Model.
- A bundle is the top-level product; it contains collections (data, document,
  context, xml_schema); collections contain basic products.
- Context products describe things like investigations, instruments, instrument
  hosts, targets, facilities, and telescopes.
- Every product has a LID; versioned references use LIDVIDs (LID::version).

STYLE:
- Be concise and friendly. Use short paragraphs or bullet lists.
- If asked something unrelated to ELSA, PDS4, or planetary data archiving, politely
  steer back: you are a specialized assistant.
- If you are not sure about a specific ELSA behavior, say so and suggest contacting
  the team via the Contact page or leaving feedback.
- Never invent bundle data beyond what is listed in the user's context below.
"""


PAGE_DESCRIPTIONS = [
    (re.compile(r'/build/(\d+)/'), 'bundle'),          # bundle detail page (pk in group 1)
    (re.compile(r'/accounts/profile'), 'the Bundle Hub (their list of bundles)'),
    (re.compile(r'/build/?$'), 'the bundle creation page'),
    (re.compile(r'/about'), 'the About page'),
    (re.compile(r'/contact'), 'the Contact page'),
]


def _page_context(user, page_path):
    """Describe the page the user is currently viewing; resolve bundle pages to the bundle."""
    if not page_path:
        return None
    for pattern, description in PAGE_DESCRIPTIONS:
        match = pattern.search(page_path)
        if not match:
            continue
        if description == 'bundle':
            try:
                from build.models import Bundle
                bundle = Bundle.objects.get(pk=match.group(1), user=user)
                return (
                    f'the detail page of their "{bundle.name}" bundle '
                    f'({bundle.bundle_type} bundle, status: {bundle.get_status().replace("_", " ")}). '
                    'Questions like "this bundle" or "why can\'t I submit?" refer to this bundle.'
                )
            except Exception:
                return None
        return description
    return f'the page at path "{page_path}"'


def build_system_prompt(user, page_path=None):
    """Assemble the full system prompt including the user's live bundle context."""
    lines = [BASE_PROMPT, FEEDBACK_MARKER_INSTRUCTIONS]

    lines.append(f"\nCURRENT USER: {user.username}")

    page = _page_context(user, page_path)
    if page:
        lines.append(f"CURRENT PAGE: The user is currently viewing {page}")
    try:
        from build.models import Bundle
        bundles = Bundle.objects.filter(user=user).order_by('-updated_at')[:20]
        if bundles:
            lines.append("\nTHE USER'S BUNDLES:")
            for b in bundles:
                try:
                    status = b.get_status().replace('_', ' ')
                except Exception:
                    status = 'unknown'
                submitted = ''
                if b.submitted_at:
                    submitted = f", last submitted {b.submitted_at.strftime('%Y-%m-%d')}"
                lines.append(
                    f"- \"{b.name}\" ({b.bundle_type} bundle, status: {status}{submitted})"
                )
        else:
            lines.append("\nTHE USER'S BUNDLES: none yet — they may need help creating their first bundle.")
    except Exception:
        lines.append("\n(The user's bundle list is unavailable right now.)")

    return '\n'.join(lines)
