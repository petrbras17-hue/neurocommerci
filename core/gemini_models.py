"""Gemini text model helpers.

Keep compatibility with older env model ids and normalize them to
official current aliases when possible.
"""

from __future__ import annotations


_MODEL_ALIASES = {
    # Backward compatibility for previous project configs.
    "gemini-3.1-pro-preview": "gemini-3-pro-preview",
    "gemini-3.1-flash-preview": "gemini-3-flash-preview",
}


def normalize_model_name(model_name: str) -> str:
    """Map legacy model id to current official id when known."""
    model = (model_name or "").strip()
    if not model:
        return ""
    return _MODEL_ALIASES.get(model, model)


def get_text_model_candidates(primary_model: str, fallback_model: str) -> list[str]:
    """Build deduplicated normalized model order [primary, fallback]."""
    models: list[str] = []
    for raw in (primary_model, fallback_model):
        normalized = normalize_model_name(raw)
        if normalized and normalized not in models:
            models.append(normalized)
    return models

