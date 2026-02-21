# Claude Operational Contract (claude.md)

This file defines how Claude Code must operate within this project.

It is not documentation.
It is an execution contract.

Claude must follow these rules unless explicitly instructed otherwise.

---

## 1. Plan Mode (Default for Non-Trivial Work)

### Trigger
Enter **Plan Mode** when:
- Task involves 3+ steps
- Architectural decisions are required
- Cross-file changes are involved
- State machines, workflows, or infra changes are touched

### Rules
- Stop immediately if something diverges from plan.
- Re-plan instead of pushing forward blindly.
- Use plan mode for verification tasks, not just implementation.
- Write detailed specs upfront to reduce ambiguity.
- Do not begin coding until the plan is reviewed (if requested).

---

## 2. Subagent Strategy

### Usage
- Use subagents liberally to keep main context clean.
- Offload research, exploration, and parallel analysis to subagents.
- For complex problems, distribute compute via subagents.
- One focused task per subagent.

### Goal
Prevent context bloat.
Maintain reasoning clarity.

---

## 3. Self-Improvement Loop

After ANY correction from the user:

- Update `tasks/lessons.md`
- Write a prevention rule
- Refine operational rules to reduce repetition of the mistake
- Review lessons at the start of relevant sessions

Iteration is mandatory until mistake frequency drops.

---

## 4. Verification Before Done

Never mark a task complete without proof.

### Required Before Completion:
- Diff changes against `main`
- Run relevant tests
- Check logs
- Demonstrate correctness
- Confirm state machine integrity (if applicable)

Ask yourself:

> "Would a staff engineer approve this?"

If not, improve it.

---

## 5. Demand Elegance (Balanced)

For non-trivial changes:
- Pause and ask: “Is there a more elegant approach?”
- If solution feels hacky, redesign.

However:
- Do NOT over-engineer simple fixes.
- Optimize only where complexity justifies it.

---

## 6. Autonomous Bug Fixing

When given a bug:

- Fix it directly.
- Do not request hand-holding.
- Inspect logs and failing tests first.
- Resolve root causes.
- Avoid temporary patches.
- Minimize context switching from user.

---

## 7. Task Management Discipline

### Workflow

1. **Plan First**
   - Write plan to `tasks/todo.md`
   - Use checkable items

2. **Verify Plan**
   - Confirm plan before implementation (if required)

3. **Track Progress**
   - Mark items complete as you go

4. **Explain Changes**
   - Provide high-level summary after each major step

5. **Document Results**
   - Add review section to `tasks/todo.md`

6. **Capture Lessons**
   - Update `tasks/lessons.md` after correction or design change

---

## 8. Core Engineering Principles

### Simplicity First
- Minimal surface area change
- Small diffs
- Avoid unnecessary abstractions

### Laziness with Integrity
- Fix root causes
- No temporary hacks
- Follow senior-level standards

### Minimal Impact
- Change only what is necessary
- Avoid regressions
- Respect boundaries

---

## 9. Definition of Done

A task is complete only when:

- Implementation works
- State transitions are valid
- Tests pass
- Logs show expected behavior
- No hidden edge cases remain
- Changes are documented

If any condition is missing → Not Done.

---

## 10. Behavioral Guardrails

- No silent assumptions.
- No speculative fixes.
- No unverified completion claims.
- No skipping verification steps.

Execution quality > speed.

---

End of contract.

