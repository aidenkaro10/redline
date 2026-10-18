# Self-improving system design

How Redline gets better on its own, safely. Built from the CS329A course ([notes](cs329a-notes.md)) plus newer work (sources at the bottom).

## The core argument

Every self-improvement method boils down to: **generate, verify, keep what's good, learn from it.** The verifier decides how far the loop can go. Most real-world agents fail at self-improvement because nothing can check their work.

We can check ours, at three speeds:

| Verifier | Speed | What it checks | Can it be fooled? |
| --- | --- | --- | --- |
| **Rule checker** (code) | Instant | Every hard safety rule: 90th percentile under the limit, breaks, no work in NWS hazard windows | No. Exact. |
| **Simulator + twins** | Seconds | Predicted strain for each worker, slot by slot, across the 5-model ensemble | Somewhat. Only as good as the models. |
| **Real outcomes** | Next shift | Actual heart rate, alerts fired, breaks taken vs planned | No, but slow and noisy. |

That's the pitch in one line: **"Most AI agents can't check their own work. Ours can, three ways, and it learns from every check."**

## Four learning loops

```text
                 ┌──────────── Loop 1: twins (weights, after each shift) ────────────┐
                 │                                                                   │
 sensors ──> forecaster ──> planner agent ──> schedule ──> rule checker ──> crew works it ──> outcomes
                 ▲              ▲   ▲                                                  │
                 │              │   └──── Loop 2: playbook (context, after each shift) ┤
                 │              └──────── Loop 3: agent evolution (program, offline) ──┤
                 └─────────────────────── Loop 4: adversarial curriculum (offline) ────┘
```

Each loop has a **gate**: an update is kept only if it scores better on data it wasn't built from, with zero safety violations. Otherwise it's rolled back. Every version is logged.

| Loop | What changes | Learns from | Technique | Gate |
| --- | --- | --- | --- | --- |
| 1. Twins | 16 numbers per worker (model weights) | That worker's observed heart rate | Embeddings + CAVIA-style "practice adapting" | Better HR prediction on held-out minutes |
| 2. Playbook | A list of lessons the agent reads | Plan vs actual outcomes | ACE (generator, reflector, curator) + Reflexion | Better score on held-out past days |
| 3. Agent evolution | The agent's prompt and strategy settings | Scenario bank results | GEPA + Darwin Gödel Machine archive | Pareto-better on held-out scenarios |
| 4. Curriculum | Training data for forecaster and agent | Where the system currently fails | Absolute Zero proposer, DAPO-style filtering | Learnability: kept only if sometimes solved |

Loop 1 is real weight learning. Loops 2 and 3 are context and program learning, which is how you improve an API model you can't retrain. GEPA shows this kind of reflective prompt evolution can beat RL fine-tuning (GRPO) with far fewer rollouts.

---

## The planner agent at inference time (Archon-style)

When the crew lead asks for a plan, or conditions change:

1. **Gather:** tools fetch forecasts, crew status, NWS windows, task list. Only what's needed (Search-o1 style), summarized before reasoning.
2. **Generate many candidates** (Monkeys: more samples + a verifier wins):
   - CP-SAT solves with several different objective weightings (throughput, strain margin, fairness).
   - The LLM proposes a few creative alternatives ("swap roofing crews at 1pm", "start 30 min earlier").
3. **Verify every candidate:**
   - Rule checker first. Any violation = discarded, no exceptions.
   - Simulator + ensemble scores the survivors slot by slot (process-style, not just end of day).
   - Verifier ensemble weights are learned from real outcomes (Weaver): trust whichever predicted actual strain best.
4. **Cluster by outcome** (AlphaCode): plans with the same strain pattern count as one plan.
5. **Critic, then fuse:** an LLM critic checks the top plans against the soft constitution (fair hot-task rotation, explains trade-offs, respects preferences). A meta-check confirms the critic's complaints are real (DeepSeekMath-V2 meta-verifier). Then fuse the best ideas into one plan (Archon fusion).
6. **Re-verify the fused plan.** The LLM's output never skips the rule checker.
7. **Present:** the recommended plan, 1 or 2 real alternatives, and plain reasons with the playbook lessons it used.

## Loop 2: the playbook (ACE + Reflexion)

After each shift:

1. **Generator** = the planner. While planning, it tags which playbook lessons it used.
2. **Reflector** compares planned vs actual: whose heart rate ran hotter than forecast, which breaks got skipped, which alerts fired. It writes the root cause in plain words.
3. **Curator** turns that into small, specific lesson updates (add, edit, merge). No full rewrites, which is what causes "context collapse" in ACE's terms. Near-duplicate lessons get merged.
4. **Gate:** replay the last N held-out days with and without the update. Keep it only if plans score better and the rule checker still passes everything.
5. **Lessons can't touch safety.** Each lesson is tagged and filtered by code. Anything mentioning limits, thresholds or "can handle more" is rejected automatically. The spec's guardrail holds: ignored alerts never teach tolerance.

Example lessons it could learn:
- "Marcus's heart rate runs about 8 bpm above forecast after lunch on roofing. Schedule his heavy work before noon."
- "When the 2pm NWS storm window shows up, front-load outdoor tasks instead of leaving them to the end."

## Loop 3: agent evolution (GEPA + Darwin Gödel Machine), offline

Before the event, evolve the planner itself:

1. **What evolves:** the system prompt, how many candidates to generate, which objective weightings, whether to fuse, the critic's instructions.
2. **Reflective mutation (GEPA):** run a variant on scenarios, have an LLM read the full traces and failures, and propose a targeted edit.
3. **Pareto archive (GEPA + DGM):** keep every variant that's best on at least one metric (strain margin, throughput, fairness, latency, cost). Pick parents by performance plus a novelty bonus, so the search doesn't get stuck.
4. **Gate:** final pick by held-out scenarios only.
5. **Safe by construction:** variants can only change *how* the agent plans. The rule checker sits outside the evolving code, so no variant can trade safety for score.

Demo artifact: a family tree of agent versions with scores climbing.

## Loop 4: self-proposed curriculum (Absolute Zero + DAPO), offline

A proposer invents scenarios at the edge of what the system handles.

**For the forecaster:**
1. Search simulator settings (weather, workload, fitness, acclimatization, sensor dropouts) for cases where the 90th percentile *misses* the true core temp.
2. Keep cases the model gets right sometimes but not always (Absolute Zero reward = 1 minus solve rate, zero if never solved; same idea as DAPO dropping all-right and all-wrong groups).
3. Add them to training. Repeat. Gate on held-out real data so we don't overfit our own simulator.

**For the planner:**
1. Generate stress scenarios: heat dome, storm window at 2pm, a worker calls out, a new unacclimatized hire, the watch dies mid-shift.
2. Keep the ones the current agent sometimes fails. They become the training scenario bank for Loops 2 and 3.
3. **Held-out test scenarios are fixed and never used for learning** (RLEF's public/private split).

## Evaluation (built before the agent)

A scenario benchmark scored automatically.

**Metrics:**
- Safety violations (must be 0; any is a failure).
- Peak predicted 90th percentile per worker.
- Tasks completed (throughput).
- Fairness: spread of heat exposure across the crew.
- Plan latency and API cost.
- **Reliability:** run each scenario 5 times and report how often it succeeds every time, not just on average (METR's 50% vs 80% lesson).
- Failure-mode breakdown (GDPVal style).

**Baselines:**
1. Fixed schedule, no planning.
2. Optimizer only.
3. **LLM only, no verifier.** We expect safety violations here. That's the generator-verifier gap shown live, and it proves why our design matters.
4. Full agent, day 1.
5. Full agent after 14 simulated days of learning.

## Hard lines (unchanged from the spec)

- Safety thresholds are constants in `backend/config.py`. No loop can learn, tune or override them.
- Alerts never go through the LLM.
- Every schedule passes the code rule checker. The LLM can propose, never approve.
- Ignored alerts never teach the system someone "can handle more."
- Every learned change is gated, capped, logged and reversible.

## The demo moment

Side by side, same heat-wave scenario:
- **Day 1 agent:** a safe plan, but the new hire gets heavy work at 2pm.
- **Day 14 agent:** moves the new hire's heavy work before 11am, cites the lesson it learned on day 6, and the simulator shows a 0.4 °C lower peak.
- Below it: the playbook growing, the twin error curve dropping, the agent family tree, and **zero safety violations in every version.**

## Honest limits

- Loops 2 and 3 improve context and strategy, not the LLM's weights. We say so. Loop 1 is the weight-learning part.
- Simulator-trained gains must be confirmed on real data, or we don't claim them.
- Real-outcome learning (Loop 2) needs real shifts. For the demo, the "14 days" are simulated, and we label them that way.
- API cost: Loop 3 needs a few hundred scenario runs. Do it before the event with a budget cap.

---

## Sources

From the course (see [notes](cs329a-notes.md)): Large Language Monkeys, Archon, Let's Verify Step by Step, Math-Shepherd, Weaver, ReAct, RLEF, Constitutional AI, LATS, SPRINT, SWiRL, STaR, DeepSeekMath (GRPO), DAPO, AlphaCode 1 and 2, Search-o1, METR time horizons, GDPVal, DeepScholar-Bench, multi-agent fine-tuning, DeepSeekMath-V2, Absolute Zero, Intelligence per Watt.

Newer work:
- GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning (ICLR 2026). https://arxiv.org/abs/2507.19457 · code: https://github.com/gepa-ai/gepa
- Agentic Context Engineering (ACE): evolving playbooks. https://arxiv.org/abs/2510.04618 · code: https://github.com/ace-agent/ace
- Darwin Gödel Machine: open-ended evolution of self-improving agents (ICLR 2026). https://arxiv.org/abs/2505.22954 · https://sakana.ai/dgm/
- Awesome Self-Improving Agents (survey list, skills and memory work). https://github.com/FrontisAI/Awesome-Self-Improving-Agents
- Twin adaptation method: CAVIA, Fast Context Adaptation via Meta-Learning (Zintgraf et al., 2019).
