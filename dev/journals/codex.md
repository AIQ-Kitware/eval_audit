# Codex developer journal — 2026 H2

Convention: append-only, one entry per session, newest at the bottom. Each entry is
a design narrative (see `AGENTS.md` § "Developer journal" and `CLAUDE.md` for the
required fields and tone).

Predecessor (2026 H1) archived at
[`archive/2026-H1-codex.md`](archive/2026-H1-codex.md).

## 2026-07-17 15:00:11 -0400

User intent: add the detailed open-weight LLM-as-a-judge experiment and
infrastructure implementation plan to
`docs/planning/open-judge-plan.md`, delivered as an overlay.

Model: GPT-5.6 Thinking.

I converted the repository-specific design investigation into a durable planning
document rather than implementation code. The document treats candidate outputs
as immutable response snapshots and judgment as a separate fan-out stage, because
that boundary is what makes judge substitution measurable and avoids rerunning or
misrepresenting candidate inference. It records the public-artifact limitation,
official annotation identity-replay gate, benchmark-faithful annotator wrappers,
judge-attributed metric requirements, infer-stack dynamic-routing topology, and a
commit-sized sequence with stop gates intended for a weaker follow-on agent.

The main risk is that several concrete live-serving details, especially Qwen
thinking-mode request controls and exact prompt context requirements, can only be
confirmed on aiq-gpu. The plan therefore labels those as explicit smoke-test gates
instead of asserting unverified server behavior. No code or existing planning
files were modified.
