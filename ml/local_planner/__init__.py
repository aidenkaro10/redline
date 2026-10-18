"""
Session I: a small open-source model (Qwen3-1.7B + LoRA) that runs on the laptop as the planner's LLM.

    scenarios.py   training scenarios (never the held-out test set)
    teacher.py     runs the Claude planner agent on them, logs every call and its cost
    dataset.py     STaR-style filter: keep only verified, high-scoring answers -> chat examples
    train.py       LoRA fine-tune with mlx-lm (adapter only)
    client.py      LocalLLM: same interface as backend/planner/llm.py's LLM (PLANNER_LLM=local)
    evaluate.py    held-out comparison: optimizer, Claude, base local, fine-tuned local

The LLM never makes safety calls. Every plan, from any model, goes through rules.py.
"""
