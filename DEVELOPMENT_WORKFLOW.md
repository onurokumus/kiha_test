# Development workflow

The objective is a reliable, polished desktop engineering analysis tool. This file is the reusable process; `TODO.md` is the ordered backlog and `IMPLEMENTATION.md` is the compact, current handoff. Preserve the desktop keyboard-and-mouse requirements in `AGENTS.md`.

## Start or resume

1. Read `AGENTS.md`, all of `TODO.md`, this file, and `IMPLEMENTATION.md` if present. Consult `CLAUDE.md` and relevant architecture documents for constraints and commands; its old phase history is not the current backlog.
2. Inspect the current branch, working-tree changes, and relevant code. Preserve unrelated user changes. Verify existing features and shared handlers before adding implementations.
3. Reconcile the handoff with actual code and Git state. Resume unfinished work first. After context compaction, use these repository files rather than chat memory; repeat investigations only when new evidence requires it.

## Select a bounded milestone

- Begin with the earliest unfinished TODO milestone whose dependencies are met. Follow phase order by default; record any justified reordering and its dependency or code-based reason.
- Select one related group of fixes or one complete feature. Split large phases into independently implementable and verifiable milestones, preserving original requirements.
- State scope and observable acceptance criteria before implementing. Complete the work rather than stopping at a plan or requesting routine approval.
- Treat TODO descriptions as simplified requirements. Fill in missing usability, robustness, accessibility, and maintainability details that directly support the milestone. Record larger unrelated ideas as proposed follow-ups; do not let them indefinitely delay the backlog.
- Make reasonable decisions autonomously and record consequential assumptions. Ask only when an unresolved decision materially changes analysis meaning, retention, compatibility, or product scope and cannot be resolved from the project. Continue independent work while awaiting an answer.

## Implement and verify

- Complete the selected milestone end to end, including relevant UI, backend, persistence, exports, and documentation. Reuse existing architecture and action handlers; avoid unrelated rewrites.
- Design for keyboard and mouse across desktop window sizes, browser zoom, maximized plots, and keyboard accessibility. Do not add touch or mobile behavior solely for compatibility.
- For scientific calculations or exported results, check methods, units, normalization, and provenance against actual code and authoritative sources.
- Run project-required checks and tests appropriate to the changes. Add meaningful regression coverage for important behavior; resolve regressions introduced by the milestone.
- For plots and interactions, verify in a browser, including applicable resize, maximize, restore, zoom, pan, and keyboard actions. Verify failure paths and existing-data compatibility where relevant. Use isolated fixtures so verification does not alter user datasets.
- Distinguish pre-existing failures from regressions. Record commands and outcomes, and label untested or blocked behavior honestly. Mark a TODO checkbox complete only after its acceptance criteria pass.

Current baseline commands (from repository root unless noted):

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests
# In frontend:
npm run build
npm run lint
```

Use the project's Python 3.13 environment on Windows (see `CLAUDE.md` native-read constraints). Browser checks and their setup should be documented with each milestone. Prefer existing available runtimes before installing dependencies.

## Checkpoint and finish

- Update `TODO.md` and `IMPLEMENTATION.md` after meaningful completed steps, before major subtask switches, and before ending. Keep checklist work in progress until verified; preserve original TODO wording when marking it complete.
- Keep the handoff concise by replacing stale status, not accumulating a transcript. Include current milestone/scope/acceptance criteria; completed and unfinished work; important decisions and reasons; relevant entry points; verification commands/results/outstanding checks; known failures/blockers and unsuccessful approaches worth avoiding; branch and relevant uncommitted changes; exact next steps and recommended next milestone. Reference detailed reports when useful.
- Continue until the selected milestone is implemented and verified or a genuine blocker prevents progress. If interrupted, checkpoint the accurate partial state so another task can resume safely.
- Review final changes for correctness, regressions, unnecessary scope, and acceptance-criteria consistency. Leave a coherent working tree and finish at this milestone boundary instead of automatically starting the remaining backlog.
- Final response: briefly explain what changed, how it was verified, remaining limitations, and the recommended next milestone.
