"""Trusted Groq endpoint resolution.

Groq model selection must never send GROQ_API_KEY to arbitrary endpoints.
The endpoint is configurable to facilitate explicit local configuration, but
only the official Groq HTTPS API endpoint is accepted.
"""
from __future__ import annotations

import os

DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def resolve_groq_base_url() -> str:
    """Return the canonical official Groq endpoint or reject unsafe overrides."""
    configured = os.getenv("GROQ_BASE_URL", DEFAULT_GROQ_BASE_URL).strip()
    if configured not in (DEFAULT_GROQ_BASE_URL, DEFAULT_GROQ_BASE_URL + "/"):
        raise ValueError(
            "GROQ_BASE_URL must be https://api.groq.com/openai/v1"
        )
    return DEFAULT_GROQ_BASE_URL
