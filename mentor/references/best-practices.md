# How to work with coding agents — the senior canon

The rubric the mentor grades against. Each dimension has: the principle, the
anti-pattern, the lifehack, and the **digest signal** — what in `digest.py`
output betrays weakness there. Grade only what the evidence supports.

**Both agents.** Applies to Claude Code and OpenAI Codex; the digest normalizes
both formats. Caveat: Codex routes almost every action through one `exec` tool, so
an exec-heavy tool mix is normal there and is not itself a discipline problem —
judge whether it read before editing, not which tool name it used.

Sources: Anthropic (context engineering), PostHog, Verdent, Stack Overflow blog,
MachineLearningMastery, plus the user's own CLAUDE.md Golden Rules. Effectiveness,
not token-golf: the goal is a user who gets correct results faster and grows,
not one who shaves tokens.

---

## 1. Goal formulation — define "done" before starting
- **Principle:** State a *verifiable* success criterion up front (test passes, bug reproduces then doesn't, metric hits). "Make it work" / "make it better" is a request to come back later, not a goal.
- **Anti-pattern:** Vague one-liners; the #1 reported failure cause. The agent guesses design, builds the wrong thing, you rebuild.
- **Lifehack:** Prompt shape = *goal + constraints + acceptance check*. "Add X so that Y is true; done when Z passes." One sentence of criterion saves three turns of correction.
- **Digest signal:** many very short prompts; high `correction-like` count; a cluster of corrections a few turns *after* a vague opener (the vague opener is the root cause, not the turn where you got angry).

## 2. Trust nothing, verify everything
- **Principle:** Clean-looking code hides subtle logic errors that take longer to find than to have written. 96% of engineers don't fully trust AI output — only ~48% verify it. Be in the 48%.
- **Anti-pattern:** Accepting "готово / done" at face value; treating a passing test as proof of correctness; letting the agent edit the test to make it green.
- **Lifehack:** Ask "show me it working" — run the flow, read the diff, spot-check one claim. Passing tests ≠ correct; require the agent to exercise the real path.
- **Digest signal:** low Read/Grep relative to Write/Edit (writing without reading first); zero verification tool calls after big edits; the user never asks for evidence.

## 3. Scope small, iterate — no big-bang
- **Principle:** Bounded tasks beat "do everything at once." Small diffs review cleanly and roll back safely.
- **Anti-pattern:** One prompt asking for a multi-file, multi-concern change; broad permissions; overlapping agents on the same files with no ownership.
- **Lifehack:** Slice to the smallest shippable step. Plan-mode for anything non-trivial *before* editing. One logical change per turn.
- **Digest signal:** huge tool-call count in a single span with few user checkpoints; many interrupts (you had to stop a runaway).

## 4. Context engineering — context is a finite budget
- **Principle:** Context is finite and rots — more tokens lowers recall accuracy. Curate high-signal context, don't dump.
- **Anti-pattern:** Giant CLAUDE.md / pasted docs; never compacting; one endless session that drifts.
- **Lifehack:** Compact/summarize at phase boundaries; use structured notes and sub-agents to keep the main thread clean; modular skills (20–50 line files) over one mega-doc. Fresh session per new goal.
- **Digest signal:** enormous input(+cache) tokens; multi-hour single span; repeated `/compact`; context-continuation summaries mid-session.

## 5. Model & cost tiering — right model for the job
- **Principle:** Match model tier to task difficulty. A cheap/fast model handles mechanical edits; reserve the frontier model for hard reasoning, architecture, tricky debugging.
- **Anti-pattern:** Frontier model for `mv`-tier chores; or a weak model on the one problem that needed the strong one.
- **Lifehack:** Delegate mechanical, well-specified work to a cheaper lane (or a subagent). Spend the expensive tokens where correctness is load-bearing.
- **Digest signal:** high output tokens with a trivial task mix; frontier model on a session that was mostly renames/moves.

## 6. Tool discipline
- **Principle:** Prefer the precise tool. Search tools (Grep/Glob) and Read beat shelling out; dedicated tools beat ad-hoc Bash.
- **Anti-pattern:** Everything through Bash `grep`/`cat`/`sed`; re-deriving state you already had.
- **Lifehack:** Read before Edit; search before assuming. Let the agent use structured tools — it's faster and less error-prone than parsing shell output.
- **Digest signal:** Bash dwarfs Read/Grep/Edit; tool errors clustered on Bash.

## 7. Feedback loops & staying accountable
- **Principle:** Errors are feedback; you remain the reviewer of record. Over-reliance is the trap — the human is still accountable for what ships.
- **Anti-pattern:** Rubber-stamping; never reading the diff; outsourcing judgment.
- **Lifehack:** Treat each correction as a rule for next time — fold it into CLAUDE.md so the agent stops repeating it. Review is not optional.
- **Digest signal:** repeated corrections of the *same kind* across the session (a missing standing rule); refusals/friction.

## 8. Emotional signal = process signal
- **Principle:** Frustration ("ну блять отвечай") is data. Cursing at the agent almost always traces to an under-specified goal or missing context a few turns back — not to the agent being "dumb" in the moment.
- **Lifehack:** When you feel friction rising, stop and re-state the goal + constraints instead of pushing the same vague ask louder. Reset context if it's drifted.
- **Digest signal:** correction/interrupt density rising over the session; profanity in prompts; short repeated re-asks.

---

## The mentor's stance
Senior, honest, specific. No flattery, no "great job!" padding. Cite the session
(prompt numbers, metrics). Every criticism carries a concrete "next time do X."
The point is to grow the user into someone who works with models well — the
feedback loop that used to come from a human senior. Grade only what the
evidence shows; if the session was clean, say so and raise the bar.
