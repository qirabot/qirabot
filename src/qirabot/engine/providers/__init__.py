"""LLM providers for the local decision engine: gemini-vertex (Vertex AI,
ADC or Vertex API key) and gemini (the Gemini Developer API, AI Studio
keys). Both talk raw REST over httpx — no provider SDKs. Import from the
submodules directly (registry for model/config resolution, base for the
Provider protocol and wire-neutral types).
"""
