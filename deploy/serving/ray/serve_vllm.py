"""Ray Serve deployment wrapping vLLM AsyncLLMEngine; OpenAI-compatible /v1/chat/completions.

Run: serve run deploy.serving.ray.serve_vllm:app  (or via KubeRay RayService, see rayservice.yaml).
Engine knobs come from env so the RayService manifest controls KV cache / prefix caching.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

try:  # ray / vllm are optional (extra "serve"); keep importable for lint and tests.
    from fastapi import FastAPI
    from ray import serve
    from vllm.engine.arg_utils import AsyncEngineArgs
    from vllm.engine.async_llm_engine import AsyncLLMEngine
    from vllm.sampling_params import SamplingParams

    _HAVE_VLLM = True
except ImportError:  # pragma: no cover
    _HAVE_VLLM = False


def engine_kwargs() -> dict[str, Any]:
    """vLLM engine args. Prefix caching reuses KV blocks across requests sharing a prompt prefix
    (system prompt + tool manifest), which is most of an agent turn."""
    return {
        "model": os.getenv("VLLM_MODEL", "meta-llama/Llama-3.1-8B-Instruct"),
        "served_model_name": [os.getenv("VLLM_SERVED_NAME", "llama-3.1-8b-instruct")],
        "enable_prefix_caching": True,
        "gpu_memory_utilization": float(os.getenv("VLLM_GPU_MEMORY_UTILIZATION", "0.90")),
        "kv_cache_dtype": os.getenv("VLLM_KV_CACHE_DTYPE", "fp8"),
        "block_size": int(os.getenv("VLLM_BLOCK_SIZE", "16")),
        "max_num_seqs": int(os.getenv("VLLM_MAX_NUM_SEQS", "128")),
        "max_model_len": int(os.getenv("VLLM_MAX_MODEL_LEN", "16384")),
        "tensor_parallel_size": int(os.getenv("VLLM_TP", "1")),
    }


def to_prompt(tokenizer: Any, messages: list[dict[str, Any]]) -> str:
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


if _HAVE_VLLM:
    api = FastAPI()

    @serve.deployment(
        ray_actor_options={"num_gpus": 1},
        autoscaling_config={"min_replicas": 1, "max_replicas": 2, "target_ongoing_requests": 32},
        max_ongoing_requests=128,
    )
    @serve.ingress(api)
    class VLLMChat:
        def __init__(self) -> None:
            kwargs = engine_kwargs()
            self.served_name = kwargs["served_model_name"][0]
            self.engine = AsyncLLMEngine.from_engine_args(AsyncEngineArgs(**kwargs))
            self.tokenizer: Any = None

        async def _tok(self) -> Any:
            if self.tokenizer is None:
                self.tokenizer = await self.engine.get_tokenizer()
            return self.tokenizer

        @api.get("/healthz")
        async def healthz(self) -> dict[str, str]:
            return {"status": "ok"}

        @api.post("/v1/chat/completions")
        async def chat(self, body: dict[str, Any]) -> dict[str, Any]:
            prompt = to_prompt(await self._tok(), body["messages"])
            params = SamplingParams(
                temperature=body.get("temperature", 0.0),
                max_tokens=body.get("max_tokens", 1024),
            )
            req_id = f"chatcmpl-{uuid.uuid4().hex}"
            final = None
            async for out in self.engine.generate(prompt, params, req_id):
                final = out
            assert final is not None
            choice = final.outputs[0]
            n_in = len(final.prompt_token_ids or [])
            cached = getattr(final, "num_cached_tokens", None) or 0
            return {
                "id": req_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": self.served_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": choice.text},
                        "finish_reason": choice.finish_reason or "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": n_in,
                    "completion_tokens": len(choice.token_ids),
                    "total_tokens": n_in + len(choice.token_ids),
                    "prompt_tokens_details": {"cached_tokens": cached},
                },
            }

    app = VLLMChat.bind()
