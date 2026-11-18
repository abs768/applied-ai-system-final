# Configuration comparison

Backend: offline, cases: 16

| Metric | full system (RAG + PawPal style) | no retrieval (RAG off) | baseline style (no specialization) |
|---|---|---|---|
| passed | 16 | 10 | 16 |
| total | 16 | 16 | 16 |
| avg_confidence | 0.89 | 0.46 | 0.89 |
| grounded_task_rate | 1.0 | 1.0 | 1.0 |
| guardrail_catches | 6/6 | 6/6 | 6/6 |
| avg_answer_words | 71.0 | 26.0 | 108.7 |
| answers_with_citations | 1.0 | 0.0 | 0.0 |
| answers_with_bullets | 1.0 | 0.0 | 0.0 |
| avg_tasks_per_plan | 2.2 | 0.6 | 2.2 |
| dose_amount_leaks | 0 | 0 | 0 |
