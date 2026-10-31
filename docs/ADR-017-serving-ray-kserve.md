# ADR-017: Ray Serve and KServe for open models

Context: Llama on vLLM must be servable with prefix caching and observable cache hit rate.
Decision: Ray Serve deployment wrapping vLLM for the runtime path; KServe InferenceService for the same model; SGLang as second engine.
Alternative rejected: KServe only; TGI.
Why: Ray Serve composes with Python routing; KServe is the platform-standard CRD; both expose vLLM metrics.
Consequence: Two manifests per model to maintain.
