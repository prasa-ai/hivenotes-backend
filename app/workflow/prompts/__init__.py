"""
app/workflow/prompts — SOAP prompt registry.

All variants live in ``variants.py``.  The registry auto-discovers them at
import time — no further registration is needed.

Adding a new prompt variant
───────────────────────────
Open ``variants.py`` and add a new entry to ``PROMPT_VARIANTS``:

    "v3_few_shot": {
        "description": "Few-shot chain-of-thought variant",
        "system_prompt": _V3_SYSTEM,
        "user_prompt":   _V3_USER,      # must contain {transcript_text}
    },

Selecting a variant at runtime
──────────────────────────────
Set the environment variable (or .env entry):

    SOAP_PROMPT_VERSION=v3_few_shot

The default is ``v2_clinical``. Any key present in ``PROMPT_VARIANTS`` is valid.

Programmatic override (e.g. in tests or evaluation scripts):

    from app.workflow.prompts import get_prompt_set
    ps = get_prompt_set("v1_basic")
    print(ps.system_prompt)
    print(ps.format_user(transcript_text))
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Optional

__all__ = ["PromptSet", "PROMPT_REGISTRY", "get_prompt_set"]


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PromptSet:
    """A matched pair of system + user prompts for SOAP note generation."""

    name: str
    """Registry key, e.g. 'v1_basic'."""

    description: str
    """Human-readable summary shown in logs and evaluation reports."""

    system_prompt: str
    """Content for the ``system`` role message."""

    user_prompt: str
    """Content for the ``user`` role message. Must contain ``{transcript_text}``."""

    output_format: str = "json"
    """
    Controls how the model response is parsed.

    ``"json"``
        The model is forced into JSON-object mode (``response_format={"type": "json_object"}``)
        and the response is parsed directly as SOAP JSON.

    ``"xml_cot"``
        Chain-of-thought mode.  The model returns free text containing a
        ``<reasoning>`` block followed by a ``<soap-notes>`` block that holds
        the JSON payload.  The node extracts and parses the JSON from inside
        ``<soap-notes>`` and discards the reasoning text.
        Use this for few-shot / chain-of-thought prompt variants.
    """

    def format_user(self, transcript_text: str) -> str:
        """Return the user prompt with ``{transcript_text}`` substituted."""
        return self.user_prompt.format(transcript_text=transcript_text)


# ── Auto-discovery ────────────────────────────────────────────────────────────

def _build_registry() -> dict[str, PromptSet]:
    """
    Walk every submodule of this package and collect PromptSet entries.

    Two authoring patterns are supported:

    **Multi-variant** (preferred — used by ``variants.py``):
        Export a ``PROMPT_VARIANTS`` dict where each value has the keys
        ``description``, ``system_prompt``, and ``user_prompt``.

    **Single-variant** (legacy — for individual ``vN_*.py`` files):
        Export ``SYSTEM_PROMPT`` and ``USER_PROMPT`` module-level constants.
        The registry key is the module name (e.g. ``v3_few_shot``).
    """
    registry: dict[str, PromptSet] = {}
    package_path = __path__  # type: ignore[name-defined]
    package_name = __name__

    for module_info in pkgutil.iter_modules(package_path):
        mod_name = module_info.name
        full_name = f"{package_name}.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
        except ImportError as exc:
            import logging
            logging.getLogger(__name__).warning(
                "prompts: could not import '%s': %s", full_name, exc
            )
            continue

        # ── Pattern 1: PROMPT_VARIANTS dict ──────────────────────────────────
        variants_dict = getattr(mod, "PROMPT_VARIANTS", None)
        if isinstance(variants_dict, dict):
            for key, spec in variants_dict.items():
                registry[key] = PromptSet(
                    name=key,
                    description=spec.get("description", key),
                    system_prompt=spec["system_prompt"],
                    user_prompt=spec["user_prompt"],
                    output_format=spec.get("output_format", "json"),
                )
            continue  # don't also check single-variant pattern for this file

        # ── Pattern 2: single SYSTEM_PROMPT + USER_PROMPT constants ──────────
        sys_prompt = getattr(mod, "SYSTEM_PROMPT", None)
        usr_prompt = getattr(mod, "USER_PROMPT", None)
        if sys_prompt is not None and usr_prompt is not None:
            description = (mod.__doc__ or "").strip().splitlines()[0] if mod.__doc__ else mod_name
            registry[mod_name] = PromptSet(
                name=mod_name,
                description=description,
                system_prompt=sys_prompt,
                user_prompt=usr_prompt,
            )

    return registry


PROMPT_REGISTRY: dict[str, PromptSet] = _build_registry()


# ── Public accessor ───────────────────────────────────────────────────────────

def get_prompt_set(name: Optional[str] = None) -> PromptSet:
    """
    Return the :class:`PromptSet` for *name*.

    If *name* is ``None`` the value of ``settings.soap_prompt_version`` is
    used, which falls back to ``"v2_clinical"`` when not set.

    Raises
    ------
    KeyError
        If *name* is not present in :data:`PROMPT_REGISTRY`.
    """
    if name is None:
        # Lazy import to avoid circular dependency at module load time.
        from app.config import settings  # noqa: PLC0415
        name = settings.soap_prompt_version

    try:
        return PROMPT_REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(PROMPT_REGISTRY))
        raise KeyError(
            f"SOAP prompt version '{name}' not found. "
            f"Available versions: {available}"
        ) from None
