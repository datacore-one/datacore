---
name: decision-board
version: 1.1.0
description: |
  Present decisions to the owner as a decision board: a local, PLUR-branded HTML
  page with one decision per row, a suggested answer on each, a note field, and a
  Save that hands the choices back to Claude as a JSON file. This skill fixes how
  the page looks and behaves. The session supplies the content, and the owner
  drives it. The page stays local and is never published to claude.ai or any
  hosted service.
  Use whenever the owner has more than one or two decisions to make: reviews
  (GTD weekly review, triage, planning), design choices, approval queues. Also
  use when the owner says "decision board", "put it on a board" or "let me
  decide on a page".
triggers:
  - decision board
  - put it on a board
  - decisions page
  - let me decide on a page
  - gtd review decisions
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
---

# Decision board

The owner makes decisions on a page, not in chat. This skill covers only presentation and behaviour. Content is whatever the session is working through, and the owner steers it.

## Stays local

- Write the page to `$DATACORE_STATE/decision-boards/<YYYY-MM-DD>-<slug>.html` (default `~/.datacore/state/decision-boards/`). The directory must be owner-only (0700), and artifacts are published atomically as 0600. This state is outside synchronized source repositories.
- Open the page with `open <path>`.
- Never publish it to claude.ai (Artifacts), a gist or any hosted URL, even privately. If a hosted page seems necessary, ask first.
- Inline all CSS and JS. Use local/system font fallbacks without external font requests. The generated page has a restrictive content policy. Reference links accept only explicit HTTP(S) URLs; executable or other URL schemes are rendered as text.

## Look: PLUR brand

- **Fonts.** Outfit for everything (weight 200 for the title, 300 for body text, 400 for labels). JetBrains Mono for ids, counts and meta lines.
- **Light mode.** Background `#fafaf9`, surface `#ffffff`, text `#1a1a1a`.
- **Dark mode.** Background `#0e0f14`, surface `#15171d`, text `#f0f0f2`. Switch with `prefers-color-scheme`.
- **Accents.** `#22d3ee`, `#f0a050`, `#a78bfa` and `#34d399`, used only to show sequence. `#e8695f` is the only status colour. The primary button is `#7c3aed`.
- **Separators.** Hairline rules between rows, not boxed cards. Uppercase labels get a little letter-spacing.

## Layout

- **Masthead:** a small uppercase eyebrow, a light-weight title, a one-sentence lede and an "as of" line. Beside it, a four-step path showing where this review stands.
- **Sticky bar:**
  - "N of M decided";
  - a "Use the suggestions for the rest" link;
  - the save status;
  - a Save button.
- **Groups.** Items sit under section headings, each with a count.
- **Rows.** Each row has, in order:
  - a short id (A1, A2…) and any links, on a mono meta line;
  - a one-line title that states the decision;
  - one or two sentences of context;
  - an optional preview block;
  - the options as stacked choices, each with a one-line consequence, with the suggested one tagged "suggested";
  - a "Note for Claude" field.
- **Noted list.** Anything that needs no decision goes in a "Noted, nothing to decide" list at the end of its section, not as a question.
- **Phone width.** At about 400px wide, everything stacks into one column.

## Behaviour

- **One decision per row.** Every row has a suggested answer, and 2–4 mutually exclusive options.
- **Choices persist as the owner clicks.** They're kept in `localStorage`, keyed by the board's slug and content-derived build identity. Reload restores choices when browser storage is available.
- **Save downloads `<slug>.decisions.json`.** Build it as a Blob and use an `<a download>` link.
  - Format: `{"board": "<slug>", "build": "<build identity>", "savedAt": "<ISO>", "decisions": {"<id>": {"choice": "<value>", "note": "<text>"}}}`. Both board and build are required when applying; choices must match the reviewed row options.
  - The status line then says where it went, for example "Saved to Downloads as <file>".
- **Embed the board's data** as JSON in `<script type="application/json" id="data">`.
  - Escape `<`, U+2028 and U+2029.
  - Build the U+2028 and U+2029 patterns with `String.fromCharCode(8232)` and `String.fromCharCode(8233)`, never as raw characters.
  - No script may contain a literal `</script`.
- **To revise a board,** regenerate from current source data. Saved choices may prefill an identical build only; changed rows, choices, source files or ledger state require a new review. Never edit or serialize the live DOM.

## Reading choices back

1. When the owner says they've saved, read `~/Downloads/<slug>.decisions.json`, or the path they give.
2. Summarise what they chose, including their notes. Change nothing yet.
3. Act only after they say "apply the decisions", and only on what they chose. Dry-run anything irreversible first.
4. Use `gtd_decision_board.py apply` for task decisions. It checks source and ledger versions, serializes mutation, and records durable per-row completion. An interrupted row is retained as pending and blocks replay; preserve its receipt, inspect the task and ledger, reconcile conflicts, then create a new board. Never delete a pending receipt to force a retry.
5. In generated next-actions files, benching retains the task as DEFERRED with an explicit future wake date. In authored files, Someday moves the full task to someday.org as a passive TODO. Planning delegation records a request for review and does not start a worker. See `.datacore/specs/decision-board-application.md`.

## Writing

- Titles state the decision in plain words. Context says why it matters now.
- Options say what happens, not how it's built.
- Links go in the meta line, not in titles.
- Never name a customer or client unless the owner has done so, and keep private detail out of anything that could leave the machine.
