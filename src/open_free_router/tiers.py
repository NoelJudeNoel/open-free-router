"""Tier-based routing: map a virtual tier ID to a sorted pool of upstream
instances, with context-window pre-filtering and per-provider expansion.

Design mirrors LiteLLM's "model group" + context_window_fallbacks pattern:
a single logical model id (e.g. ``glm-5.2``) may exist under several
providers; a tier collects those logical ids and expands each one into
its concrete upstream instances, then returns the candidates ordered
by priority (strongest/best-context first).
"""
from __future__ import annotations

from dataclasses import dataclass

from .registry import ModelInfo, ProviderConfig, Registry


# ── Tier -> logical model id list ────────────────────────────────────────
# A logical id matches a provider's ModelInfo.id. Same logical id may appear
# under multiple providers (that is the whole point of fallback pools).
TIERS: dict[str, list[str]] = {
    # High: flagship class / 1M context (or the strongest tier of each family)
    "high": [
        "glm-5.2",
        "deepseek-v4-flash",
        "gemini-3.6-flash",
    ],
    # Mid: strong models, ~256k-512k context or solid coding/reasoning
    "mid": [
        "minimax-m3",
        "step-3.7-flash",
        "laguna-s-2.1",
        "laguna-xs-2.1",
        "mimo-v2.5-free",
        "ling-3.0-flash",
        "nemotron-3-ultra-550b-a55b",
    ],
    # Low: everything else (small / limited-context / fallback)
    "low": [],  # filled on demand from "all minus high/mid" -- see low_members()
}

# Tier ids exposed to agents (the virtual model names)
TIER_IDS = ("tier/high", "tier/mid", "tier/low")

# Sensible fallback ordering within a tier: we want the most-capable /
# widest-context instance first so the user lands on the best available.
# Order below is "preferred instance" for each logical id; anything not
# listed falls back to insertion order from the registry.
_INSTANCE_PRIORITY = {
    ("glm-5.2", "sensenova"),      # 1M context
    ("glm-5.2", "nvidia-nim"),
    ("deepseek-v4-flash", "sensenova"),   # 128k
    ("deepseek-v4-flash", "nvidia-nim"),
    ("deepseek-v4-flash", "opencode-zen-free"),
    ("nemotron-3-ultra-550b-a55b", "openrouter"),   # 1M
    ("nemotron-3-ultra-550b-a55b", "nvidia-nim"),   # 1M (nemotron reasoning)
    ("nemotron-3-ultra-550b-a55b", "opencode-zen-free"),
}


@dataclass(slots=True)
class UpstreamInstance:
    """A concrete upstream model deployment bound to one provider."""

    provider: ProviderConfig
    model: ModelInfo
    # effective routing identity (provider-name, upstream_id) used as the
    # cooldown key and for building the upstream request payload.
    key: str
    # cached resolution of what we send to upstream as the model id
    upstream_model: str
    context_window: int
    max_tokens: int
    reasoning: bool

    @classmethod
    def for_provider(cls, provider: ProviderConfig, model: ModelInfo) -> "UpstreamInstance":
        uid = model.effective_upstream_id
        return cls(
            provider=provider,
            model=model,
            key=f"{provider.name}/{uid}",
            upstream_model=uid,
            context_window=model.context_window,
            max_tokens=model.max_tokens,
            reasoning=model.reasoning,
        )


def _normalize(uid: str) -> str:
    """Normalize an upstream_id or model id to a comparable bare name.

    Strips a leading ``provider/`` prefix so that e.g.
    ``minimaxai/minimax-m3`` matches the logical id ``minimax-m3``.
    """
    if "/" in uid:
        uid = uid.rsplit("/", 1)[-1]
    return uid


def _expand_logical(logical_id: str, registry: Registry) -> list[tuple[int, str, ProviderConfig, ModelInfo]]:
    """Find every (provider, model) whose ModelInfo.id or normalized
    upstream_id matches `logical_id`.

    Matching is exact on model.id, OR exact on the bare name part of
    effective_upstream_id (so ``nvidia/nemotron-3-ultra-550b-a55b`` matches
    the logical id ``nemotron-3-ultra-550b-a55b``).

    Returns tuples of (priority, key, provider, model) sorted best-first.
    priority is the index in _INSTANCE_PRIORITY (lower = better); unlisted
    instances sort after listed ones, preserving registry order.
    """
    found: list[tuple[int, str, ProviderConfig, ModelInfo]] = []
    for provider in registry.providers.values():
        for model in provider.models:
            uid = model.effective_upstream_id
            if model.id == logical_id or _normalize(uid) == logical_id:
                pass  # matched -> fall through to priority/sort + append
            else:
                continue
            listed = any(
                p == provider.name and uid == model.effective_upstream_id
                for p, uid in _INSTANCE_PRIORITY
            )
            priority = next(
                (i for i, (p, uid) in enumerate(_INSTANCE_PRIORITY)
                 if p == provider.name and uid == model.effective_upstream_id),
                1000,
            )
            found.append((priority, f"{provider.name}/{model.effective_upstream_id}", provider, model))
    if not found:
        # allow fallback matching by upstream_id substring for cross-aliases
        return []
    found.sort(key=lambda t: (t[0], t[1]))
    return found


def tier_members(tier: str, registry: Registry, request_context: int = 0) -> list[UpstreamInstance]:
    """Return the ordered candidate pool for a tier.

    - tier is one of TIERS keys
    - request_context (tokens) filters out instances whose context_window
      is too small (context_window_fallbacks pre-check)
    - within the tier, preferred instances come first
    """
    logical_ids = list(TIERS.get(tier, []))
    if tier == "low":
        # low = everything in the registry not claimed by high/mid
        claimed = set(TIERS["high"] + TIERS["mid"])
        logical_ids = []
        for p in registry.providers.values():
            for m in p.models:
                if m.id not in claimed and m.id not in logical_ids:
                    logical_ids.append(m.id)

    pool: list[tuple[int, UpstreamInstance]] = []
    for lid in logical_ids:
        for priority, _key, provider, model in _expand_logical(lid, registry):
            if model.context_window < request_context:
                continue
            inst = UpstreamInstance.for_provider(provider, model)
            pool.append((priority, inst))
    # Sort by priority (preferred instances first), then by largest
    # context_window as a tiebreaker so the most capable instance within a
    # logical model is attempted first (e.g. sensenova/glm-5.2 1M before
    # nvidia-nim/glm-5.2 128k).
    pool.sort(key=lambda t: (t[0], -t[1].context_window))
    return [inst for _, inst in pool]


def is_tier_id(model: str) -> bool:
    return model in TIER_IDS
