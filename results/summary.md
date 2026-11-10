| Config | Eval score | Success | Cost/task | p95 | Cache hit |
|---|---:|---:|---:|---:|---:|
| v1: Sonnet | 0.924 | 0.88 | $0.00384 | 5.4 s | 0.92 |
| v2-bad | 0.801 | 0.66 | $0.01932 | 7.1 s | 0.00 |
| v3: Haiku | 0.875 | 0.78 | $0.00018 | 2.8 s | 0.92 |
| GPT-5 | 0.906 | 0.84 | $0.00364 | 7.9 s | 0.92 |
| Gemini 2.5 Pro | 0.877 | 0.80 | $0.00444 | 6.1 s | 0.92 |
| Llama 3.1 8B (vLLM) | 0.835 | 0.64 | $0.00006 | 3.1 s | 0.92 |
| Qwen 2.5 7B (SGLang) | 0.790 | 0.58 | $0.00006 | 2.9 s | 0.92 |
| v1, caching off | 0.924 | 0.88 | $0.01285 | 5.5 s | 0.00 |
| v1, retrieved context | 0.870 | 0.82 | $0.00377 | 5.4 s | 0.80 |

| Rollout experiment | Result |
|---|---|
| Time to detect v2-bad, first canary step 10% / 25% | 97 s / 48 s |
| False rollbacks over 13 candidates: Pareto / thresholds + noise band / fixed thresholds | 0 / 1 / 11 |
| Chaos time to detect: degraded prompt / +1500 ms latency / killed tool | 134 s / 97 s / 44 s-70 s |

| Haiku (v3), 50 tasks | Mock | Real Claude API |
|---|---:|---:|
| Eval score | 0.875 | 0.892 |
| Success | 0.78 | 0.80 |
| Cost/task | $0.00018 | $0.00064 |
| p95 latency | 2778 ms | 6788 ms |
| Cache hit | 0.92 | 0.81 |
| Tool error rate | 0.015 | 0.126 |
