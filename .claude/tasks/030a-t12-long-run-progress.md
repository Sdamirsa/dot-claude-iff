# Task: T12 - long-run progress: live bars in the console, periodic report in chat

_Created 2026-10-05 · Status: todo_

Milestone: M-0.3.0-alpha · Closes: maintainer request 2026-10-05 · Shared contracts: `030a-00-milestone.md`

## Goal

During a run that lasts hours, the maintainer can see at a glance how far the work is, what
is in flight, and what is waiting on them, without asking: live in the console, and as a
compact block in the chat every N minutes. Most important in `fableous-orchestrated` mode.

**Definition of done:** `statectl progress [--json]` computes one progress model from the
journal and the task files: current milestone, its tasks with status, per-task checklist
counts (checked / total Plan items), agents in flight with elapsed time, open needs-human
items, open proposals count, run start and elapsed, last activity age, mode and phase; the
text form is a compact block with bars that fits a chat message; the console shows the same
model as a Progress panel at the top of the NOW tab (milestone bar, one row per task with a
mini bar and status chip, "needs you" list, agents in flight) and updates on the existing
live poll; an activity pulse refreshes `heartbeat.json` during a long turn (throttled by a
knob) so "last activity" is true between Stop events; in `guided-solo` and
`fableous-orchestrated` modes a periodic report (knob in minutes, 0 = off) hands the lead the
compact block with an instruction to post it to the user; everything degrades to an honest
empty state when there is no milestone or no tasks.

**Test:** `python3 .claude/tools/tests/run_tests.py test_progress test_console test_hooks -q`

## Context

- What exists: the console already polls `/live/console.json` every few seconds when served
  live, so a new payload key renders live for free. The heartbeat is written only by the Stop
  hook, so during a multi-hour turn it goes stale; that is the gap behind "heartbeat also
  needs auto-update". In-flight agents are read from handshake stubs (`_read_in_flight`).
- Depends on T4 (tasks registered under a milestone, mode/phase) and T5 (stub field agreed,
  nudge hook infrastructure). Runs after both are merged.
- Pulse: do not add a new process per tool call. The policy gate already runs on every
  Write/Edit/Bash/PowerShell call; let it touch the heartbeat when the last write is older
  than the knob, wrapped so any failure is swallowed (telemetry fails open; the gate's
  decision must be computed first and never depend on the pulse). Also pulse on
  SubagentStart/SubagentStop where a hook already runs.
- Periodic report: no hook event fires on a timer. Use the same advisory channel as the
  delegation nudge: when a hook runs and the last report is older than the knob, attach the
  compact block as context for the lead. Advisory only, never blocks, state kept in
  `.claude/state/` (declare the store).
- The console never changes state; it only shows commands to copy.
- One progress model, two renderings (text, console). The percentage is computed from Plan
  checklist items across the milestone's tasks, with task status as the tie-breaker; say in
  the output what the number counts.

## Plan

- [ ] Progress model + `statectl progress` text/JSON, done when: fixture with a milestone, mixed task states and checklists gives the expected numbers
- [ ] Console Progress panel on NOW, done when: console tests incl. empty project and a no-milestone project
- [ ] Activity pulse in the gate (throttled, fail-open) and on subagent events, done when: hook tests show the heartbeat refreshes after the throttle window, not before, and a broken pulse never changes the gate decision
- [ ] Periodic report channel, done when: hook tests cover due / not due / knob 0 / freestyle off
- [ ] Orchestration protocol and project-memory skill mention it; cards, registry knobs, probe, done when: `test_contracts` green

## Checkpoint

- **Last completed:** none
- **Next action:** dispatch builder after T4 and T5 merge
- **State files:** `.claude/state/handshakes/T12.json`
- **Updated:** 2026-10-05

## NEEDS-HUMAN

| Id | Date | Question | Options | Recommendation | Blocks |
|----|------|----------|---------|-----------------|--------|

## Outcome

