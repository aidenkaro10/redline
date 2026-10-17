# CS329A (Stanford, Self-Improving AI Agents): notes for PlanIT Control

Condensed from the lecture transcripts Aiden shared. Our own summary, not the lectures themselves. Each section ends with what it means for our build.

**The one idea that runs through the whole course:** self-improvement is a loop. Generate something, check it, keep what's good, learn from it. **How good your checker (verifier) is decides how far the loop can go.** Math and code work because answers can be checked. Most domains stall because they can't.

**Why that's great news for us:** our planner works in a domain we *can* check. A schedule can be tested three ways: exactly (hard safety rules in code), quickly (run it through the simulator and the worker twins), and for real (what actually happened on the next shift). That puts us in the small set of domains where self-improvement really works.

---

## L1. Intro: from LLMs to agents

- Scaling made models better; chain-of-thought, instruction tuning and RLHF made them useful.
- Inference is now a third lever: spend more compute at answer time.
- **Agent** = goal + actions in an environment + feedback + knowing when to stop.
- Common workflow building blocks: prompt chaining, routing, parallel calls + aggregation, an orchestrator that plans, an evaluator/judge, verifiers.
- **Generator-verifier gap:** models can generate good answers far more often than anyone can reliably pick them out.

**For us:** the planner is an orchestrator agent with tools. Our edge is that we close the generator-verifier gap with real verifiers, not an LLM guessing.

## L2. Inference-time scaling

- **Large Language Monkeys:** sample many times and keep what a verifier accepts. Coverage keeps climbing with samples (a power law, driven by a long tail of hard problems). Small models + many samples + a good verifier can beat big models.
- **Without a verifier**, majority voting plateaus fast (around 10 to 50 samples). The rare correct answer gets outvoted.
- **Parallel vs sequential:** sampling many answers vs revising one. Easy problems favor revising; hard problems need more parallel exploration.
- **PRM-guided beam search:** score partial solutions step by step and expand only the promising ones.
- **Archon:** stack inference "layers" (generate, critic, rank, **fuse**, verify, unit-test) and search for the best stack under a budget. Fusing several answers into one beat even oracle selection of the best single answer.

**For us:** the planner generates many candidate schedules, verifies all of them, critiques, fuses the best into one, and re-verifies. A cheap version of Archon with a real verifier at the end.

## L3. Verification

- **Outcome verifiers (ORM):** score the final answer. Gains stop past a few hundred samples because the verifier can't tell near-identical answers apart.
- **Process verifiers (PRM):** score each step. Fewer "right answer, wrong reasoning" false positives, more data-efficient, generalize better. Expensive human step labels (PRM800K).
- **Math-Shepherd:** label steps automatically by rolling out from each step and checking how often you reach a correct answer. No humans needed. Weakness: hard problems give no signal.
- **Weaver:** combine many imperfect verifiers. Filter out bad ones, learn a weight per verifier from a small labeled set, then distill the whole ensemble into one small fast model.

**For us:** our "verifier" for a schedule is an ensemble: the 5 forecaster models, the simulator, a worst-case twin, and the exact rule checker. Learn how much to trust each from real outcomes (Weaver). Score plans step by step (per 30-min slot), not just at the end of the day (PRM idea).

## L4. Feedback from tools and code

- **ReAct:** interleave thinking and acting. Grounding in real observations cuts hallucination.
- **RLEF:** improve code generation using execution feedback. Key trick: **public tests** give feedback during the loop, **private tests** decide the reward, so the model can't overfit what it sees.
- **Constitutional AI:** humans write principles; the model critiques and revises its own outputs against them; train on the revisions. Scales feedback without armies of labelers.

**For us:** the planner uses tools ReAct-style. Split scenarios into a **training set** the agent learns from and a **held-out test set** it never sees (the RLEF public/private split). Two-tier constitution: safety rules enforced by code (never the LLM), soft principles (fair rotation of hot tasks, explain trade-offs) checked by an LLM critic.

## L5. Planning and multi-step reasoning

- **LATS:** Monte Carlo tree search over agent actions. Select by UCT (balance best-so-far against unexplored), score states with an LLM judge + self-consistency, back up results, add written reflections after each trajectory.
- **SPRINT:** teach models to spot independent plan steps and run them in parallel. Faster, and accuracy went up too.
- **SWiRL:** generate multi-step tool-use trajectories offline, have an LLM judge score each step, then do step-wise RL. **Process-filtered data beat outcome-filtered data**, and skills transferred across tools and datasets.

**For us:** OR-Tools already does exact combinatorial search, so we skip MCTS for scheduling. We keep two ideas: reflections after each run, and judging the agent step by step. Run independent what-if simulations in parallel.

## L6. Train-time scaling (RL)

- **STaR:** generate reasoning, keep only what leads to correct answers, fine-tune, repeat. For failures, show the answer and ask the model to explain backwards ("rationalization").
- **GRPO (DeepSeekMath):** sample a group of answers, normalize rewards within the group, no critic model needed.
- **DAPO:** tricks that make RL stable on long reasoning: asymmetric clipping (keeps exploration alive), **dynamic sampling** (drop groups that are all right or all wrong, since they carry no signal), token-level loss, soft penalties for overly long outputs.
- RL mostly makes models more *consistent* (majority@k goes up), not fundamentally smarter (pass@k barely moves).

**For us:** we can't fine-tune Claude's weights, so we don't RL the LLM. We do use the core lesson: **train where the model sometimes fails.** All-easy or all-impossible examples teach nothing. That drives our adversarial curriculum for the forecaster and the scenario curriculum for the planner.

## L7. Search at scale

- **AlphaCode:** sample up to a million programs, filter by the example tests, **cluster by behavior** (run them on generated inputs and group programs that give the same outputs), submit one per cluster.
- **AlphaCode 2:** better base model, a family of fine-tuned variants for diversity, and a learned scoring model. Same performance with ~10,000x fewer samples.
- **Search-o1:** retrieve only when the model is uncertain, then reason over the retrieved documents and keep only the relevant parts before continuing.

**For us:** cluster candidate schedules by *outcome* (same predicted strain pattern = same plan in practice) and show the crew lead one per cluster, not 50 near-duplicates. The agent fetches NWS and crew data on demand and summarizes it before reasoning.

## L8. Evaluating agents

- **METR time horizons:** task length agents can finish keeps doubling, but the gap between 50% and 80% reliability is large.
- **GDPVal:** win rate against real experts on real work. Top failures: ignoring instructions, skipping the reference data, formatting.
- **DeepScholar-Bench:** research synthesis; agents miss key facts and important sources even when the writing is fluent.
- Always do failure-mode analysis; report reliability, not just average score.

**For us:** build a scenario benchmark for the planner before building the planner. Report reliability over repeated runs, a failure-mode breakdown, and comparisons against simpler baselines.

## L9. Wrap-up and open directions

- **Multi-agent fine-tuning:** several specialized generators + critics keep outputs diverse; one model fine-tuned on itself collapses.
- **DeepSeekMath-V2:** a **meta-verifier** checks whether the verifier's complaints are real, which stops verifiers from hallucinating problems.
- **Absolute Zero:** the model proposes its own tasks and solves them. The proposer is rewarded for tasks of *moderate* difficulty (reward = 1 minus solve rate, zero if never solved). The curriculum grows as the solver improves.
- **Intelligence per watt:** small local models now handle most everyday queries; energy is the new constraint.

**For us:** a proposer that invents harder heat-stress scenarios at the edge of what our system handles (Absolute Zero), for both the forecaster and the planner. A meta-check on the LLM critic's complaints. The live safety path (Kalman + forecaster + alerts) runs locally on the laptop with no internet, which is also a good intelligence-per-watt story.
