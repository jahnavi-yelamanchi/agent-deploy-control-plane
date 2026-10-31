# Serving cache settings

| Layer | Setting | Where |
|---|---|---|
| Provider prompt caching | `params.prompt_caching: true` in AgentSpec; cache_read/cache_write tokens in `adcp_tokens_total{type}` | specs/ |
| vLLM KV cache | `gpu_memory_utilization=0.90` sizes the paged KV pool; `kv_cache_dtype=fp8` halves bytes/token; `block_size=16` tokens/page; `max_num_seqs=128` caps concurrent sequences | ray/serve_vllm.py, kserve/inferenceservice.yaml |
| vLLM prefix caching | `enable_prefix_caching=True`: hash-matched full KV blocks reused across requests sharing system prompt + tool manifest | same |
| SGLang radix cache | RadixAttention tree over token prefixes, on by default; `--schedule-policy=lpm` orders the queue by longest cached prefix | sglang/deployment.yaml |

Cache hit rate:
- vLLM: `sum(rate(vllm:prefix_cache_hits_total[5m])) / sum(rate(vllm:prefix_cache_queries_total[5m]))`; KV pool pressure: `vllm:gpu_cache_usage_perc`.
- SGLang: `sglang:cache_hit_rate` gauge.
- Hosted APIs: `sum(rate(adcp_tokens_total{type="cache_read"}[5m])) / sum(rate(adcp_tokens_total{type=~"input|cache_read"}[5m]))`.
