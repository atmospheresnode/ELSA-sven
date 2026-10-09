<!-- watches: templates/base-derk.html#nav, friends/views.py#bundle_hub, templates/friends/bundle_hub.html, build/urls.py -->
<!-- fingerprint:
     templates/base-derk.html#nav      = 568cd6ce6baa
     friends/views.py#bundle_hub       = 02a643915c11
     templates/friends/bundle_hub.html = 1769e80d5787
     build/urls.py                     = d97e97aeb9bb
-->
<!-- reviewed: 2026-09-25 -->
<!-- baseline: ab6b3482d9893d5e8b7e506fadff1e8125905f6a -->
# Navigating ELSA

Key pages:
- Bundle Hub (under your username menu, top right), lists all your bundles as
  cards with search, a type filter (Archive / External), status badges, and
  stats (total / archive / external counts). New bundles are created from here.
  Hovering a card reveals a checkbox; selected bundles can be bulk-deleted with
  the Delete button (with Select all and a confirmation modal). A red "Files
  missing" badge means ELSA cannot find that bundle's files on disk; contact
  the ELSA team before deleting it.
- Account (under your username menu, top right), your profile and all
  settings on one page: each row (name, email, username, password, passkeys)
  has a button that opens a small window. Email changes are confirmed with a
  6-digit code sent to the new address. Agency changes are requested from the
  ELSA team with an editable drafted email. The Close account section is at
  the bottom.
- Bundle page, the workspace for one bundle. Shows the bundle name and Bundle
  ID (LID), Download and Delete buttons, the Bundle Components card (Alias,
  Modification History, Citation Information, Targets, and the Review & Submit
  button), and the file/collection sections below.
- About page, release notes for current and recent ELSA versions.
- Contact page, reach the ELSA / Atmospheres node team directly.
- ELSA Assistant, the chat widget at the bottom right of every page (this
  assistant); it answers questions about using ELSA and can pass feedback to
  the team. Replies can be copied and rated (thumbs up/down); a thumbs-down
  asks what went wrong, and the ELSA team reviews those. Enter sends a
  message, Shift+Enter starts a new line, and the send button becomes a stop
  button while a reply is streaming. Chats are stored to help improve ELSA.

Common actions:
- Create a bundle: Bundle Hub -> create, choose Archive or External and a name
  (the name determines the auto-generated Bundle ID / LID).
- Download a bundle: the green Download Bundle button on the bundle page
  produces the bundle directory with all generated PDS4 XML labels.
- Delete a bundle: red Delete Bundle button on the bundle page (confirmation
  modal), or bulk-delete several at once from the Bundle Hub via the card
  checkboxes. Both remove the bundle and its files permanently.
- Get back to a bundle: any page about one bundle (a document editor, a context
  product page) has a "Back to <bundle name>" link at the top that returns
  to it. Saving from a bundle page window or an Edit link comes back to the
  bundle page; if the save is refused, the window stays open with the reason
  under the field and nothing typed or chosen is lost.
- Guided walkthrough: ticking the walkthrough box when creating a
  bundle opens the walkthrough, one page with a list of steps on the left
  (Archive: Alias, Citation, Investigation, Targets;
  External: Citation, Targets, Documents, Data) and a Finish step that lists
  what is still needed before Review & Submit. Steps can be done in any order;
  each says Added, Missing or Optional. Each step saves when its button is
  pressed and the page stays where it is; "Skip for now" moves on without
  saving. "Go to bundle page" (top right) leaves at any time. The setup is only
  offered when a bundle is created; afterwards everything in it is done from the
  bundle page itself.
- Get help or report problems: ask this assistant, or use the Contact page. The
  assistant can also send feedback (bugs, suggestions) to the ELSA team for you.
