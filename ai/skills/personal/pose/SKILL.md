---
name: pose
description: >
  Study companion and progress tracker for the POSE (Process Oriented Systems Engineering)
  course notes in Fabian's Obsidian vault at ~/Documents/vault/my_vault. Use to survey
  note and Anki-card coverage against the lecture plan, find gaps, quiz for the written
  exam, explain topics, run mock exams, and author new notes/flashcards in the vault's
  exact conventions. Triggers: POSE, Process Oriented Systems Engineering, Weske, BPMN,
  Petri/Workflow nets, fCM, process mining, exam prep, "quiz me", "make cards".
---

# POSE — Process Oriented Systems Engineering (exam study companion)

HPI Master course by **Prof. Dr. Mathias Weske** (tutor: Anjo Seidel). This skill helps
Fabian revise for the **written exam: 90 minutes, pen & paper only, 100% of the grade**.
No aids — everything must be recallable and drawable by hand (BPMN diagrams, Petri nets,
decision tables, reachability graphs, DFGs). Optimise revision for *doing*, not just recognising.

## Vault facts

- **Vault:** `/Users/fabiankraus/Documents/vault/my_vault` (flat — no folders; notes for other
  courses like Network Security, Azure, Bitcoin/Lightning live alongside POSE notes).
- **Identify POSE notes** by the frontmatter tag `lecture: "[[Process Oriented Systems Engineering|POSE]]"`.
  Never assume a note is POSE from its title alone — filter by this tag.
- **Source of truth for progress** is the MOC note `Process Oriented Systems Engineering.md`.
  It holds two tables: a weekly *Status* table (attendance/material/quiz/anki) and a
  *Status Lectures* table mapping every lecture unit (1.1 … 9.9) to a note and a DONE marker.
- **Git:** the `obsidian-git` plugin auto-commits ("vault backup: …"). No need to commit;
  just edit files directly. Do **not** create a CLAUDE.md — this skill is the entry point.

## Get the overview first

Always start a study session by running the overview script — it lists every POSE note,
its `#card` count, sync status, and which notes still have zero cards:

```bash
bash ~/.config/ai/skills/personal/pose/scripts/overview.sh
```

Then read `Process Oriented Systems Engineering.md` to see which lecture units are DONE vs.
still missing a note, and which weeks Fabian did not attend (those need extra attention).
Cross the two: **lecture in MOC but no note**, or **note with 0 cards**, are the gaps.

### Fetch the lecture slides

The MOC's *Status Lectures* table links every lecture PDF on HPI Moodle. To pull them all
locally (in lecture order) and merge into one PDF:

```bash
bash ~/.config/ai/skills/personal/pose/scripts/download_slides.sh
```

The Moodle URLs require auth: the script reads a `MoodleSession` cookie from
`$MOODLE_SESSION` or `~/.config/moodle/hpi_session`. It re-reads URLs from the MOC each run
(stays in sync), validates each file is a real PDF, is resumable, and merges with `pdfunite`
into `~/Documents/POSE-slides/POSE-all-slides.pdf`. If files come back "not a PDF", the
cookie is stale — refresh it and re-run.

## Exam scope (lecture map)

The course is one long BPM lifecycle. Group revision by these blocks (unit numbers ↔ MOC):

1. **BPMN foundations** (1.1–1.3): activities & the [[Activity Model]], activity lifecycle,
   structuring processes into a [[Business Process Model]].
2. **BPMN in depth** (2.1–2.4): [[Gateway]]s (XOR/AND/OR/event-based), [[Event]]s
   (start/end/intermediate/boundary, interrupting vs non-interrupting, racing events),
   [[Data in Processes]], interacting business processes (message flow, pools/lanes).
3. **Decisions** (2.5–2.7): decision logic, decision tables (DMN), decision-table analysis
   (completeness, consistency, hit policies). Notes: `Decision Table.md`, `DMN.md`.
4. **Process elicitation** (3.1–3.2): process requirements, order-to-cash, tangible BPM
   ([[tangible Business Process Modelling|t.BPM]]).
5. **Case management** (4.1–4.3): multimodel consistency, [[fragment-based Case Management|fCM]],
   domain models & object behavior, [[Guard-Stage-Milestone]], [[Business Artifact]].
6. **Process model analysis** (5.1–5.11): [[Petri Net]]s, translational semantics
   (tasks, events, XOR, boundary events, concurrency, message flow), [[Workflow Net]]s,
   reachability graph / state space, **soundness** & decision soundness, compliance.
   *This is the most technique-heavy, exam-friendly block — practise translating BPMN → Petri
   net and checking soundness by hand.*
7. **Simulation** (6.1–6.3): [[Process Simulation]] — activities → processes → simulation models,
   resources, tokens, distributions.
8. **Execution** (7.1–7.3): [[Process Configuration]], [[Business Process Management System|BPMS]],
   [[Process Enactment]] (Camunda).
9. **Process mining** (8.1–8.10): event logs, data → events, instance correlation & traces,
   process discovery, **directly-follows graphs**, the **α-miner**, enhancement, conformance checking.
10. **Choreographies** (9.1–9.9): interacting processes, collaboration diagrams, sync vs async
    communication, public/private processes, consistency & compatibility, choreography diagrams,
    **local enforceability** (incl. with gateways).

When quizzing, weight toward the constructive/analytic skills (draw a diagram, build a table,
compute a graph, prove soundness) since the exam is pen-and-paper.

## Anki card conventions (flashcards-obsidian plugin)

Cards sync to the local Anki deck **`Master::POSE`** via the `flashcards-obsidian` plugin.
Settings: `flashcardsTag: card`, `deck: Master::POSE`, `contextAwareMode` on
(`contextSeparator: " > "`), `inlineSeparator: ::`, reverse `:::`, `inlineID` on.

- **Heading card (the style Fabian uses):** any heading ending in ` #card` becomes a card.
  Front = the heading text (prefixed with the note/section context); back = everything under
  that heading until the next heading of equal or higher level, including embedded images.
  ```markdown
  ### Start Event #card
  (Catching) New process is instantiated
  ![[Pasted image 20260525122355.png]]
  ```
- **Inline card (supported, not currently used):** `Question::Answer` (one-way) or
  `Question:::Answer` (front+back reversed).
- **Deck override:** set `cards-deck: Master::POSE` in frontmatter (some notes rely on the
  plugin default — include it when creating notes to be safe).
- **Sync state:** after a sync the plugin appends an id comment (`ankiID`) beside each card.
  None of the notes currently contain these → **the cards are authored but not yet pushed to
  Anki.** The overview script's "Synced" column tracks this. Syncing itself is a manual step
  Fabian does in Obsidian (⌘-P → *Flashcards: sync*) — this skill only authors the markdown.

## Authoring notes & cards (match the house style)

New POSE note skeleton:

```markdown
---
tags:
lecture: "[[Process Oriented Systems Engineering|POSE]]"
cards-deck: Master::POSE
---
# <Note Name>
```

House style observed in the vault:
- H1 title matches the filename; wikilinks `[[Note]]` / `[[Note|alias]]` for cross-refs.
- Images are pasted screenshots embedded as `![[Pasted image <timestamp>.png]]`. When a card
  needs a diagram Fabian hasn't captured yet, **leave a `> TODO: add diagram` placeholder**
  rather than inventing an image link.
- Concise bullets; **bold** for term names; `NOTE:` / `Example:` lead-ins are common.
- Put ` #card` on the heading of each atomic, testable concept.

When asked to **make cards**: append well-formed `#card` sections to the *correct existing
note* (or draft a new note), then show Fabian the diff and remind him to sync in Obsidian.
Prefer many small atomic cards over one large card. Don't fabricate facts — pull from the
note; if a note is thin, say so and offer to expand it from the lecture topic.

## Study modes (offer these)

- **Progress / gaps** — run the overview, compare against the MOC, list uncovered lectures and
  zero-card notes; recommend the highest-leverage next study action.
- **Quiz me** — pick a topic (or "weakest gaps"), read the note's `#card` headings, ask the
  front, let Fabian answer, then reveal and grade against the back. Track misses in the session.
- **Explain** — teach a concept from the notes, then go beyond them (worked example, common
  exam traps). Offer to save the improved explanation back as cards.
- **Mock exam** — generate a timed, pen-and-paper style question set matched to the exam
  (e.g. "model this scenario in BPMN", "translate to a Petri net and check soundness",
  "complete this decision table", "run the α-miner on this log", "check local enforceability").
  Then mark Fabian's answers.
- **Fill gaps** — draft a note for an uncovered lecture unit, or add cards to a zero-card note,
  in the conventions above.

Keep sessions active: make Fabian produce diagrams/tables/derivations, not just recall definitions.
