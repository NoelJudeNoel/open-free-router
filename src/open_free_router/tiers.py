"""Tier-based routing: map a virtual tier ID to a sorted pool of upstream
instances, with context-window pre-filtering and per-provider expansion.

Design mirrors LiteLLM's "model group" + context_window_fallbacks pattern:
a single logical model id (e.g. ``glm-5.2``) may exist under several
providers; a tier collects those logical ids and expands each one into
its concrete upstream instances, then returns the candidates ordered
by priority (strongest/best-context first).

---

Suffix normalisation (2026-08-14):
``_normalize()`` now strips ``:free`` and ``-free`` suffixes from
upstream_ids so that free-tier variants like
``stepfun/step-3.7-flash:free`` (Nous) or ``deepseek-v4-flash-free``
(OpenCode Zen) are recognised as the same logical model as
``step-3.7-flash`` and ``deepseek-v4-flash``. Without this,
6 cross-provider failover candidates were silently excluded from their
tiers and only reachable (out of order) in the low catch-all pool.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .registry import ModelInfo, ProviderConfig, Registry


# Strip trailing -MMDD / -YYYYMMDD date suffixes (e.g. deepseek-v4-flash-0731
# -> deepseek-v4-flash) so dated builds match their canonical tier logical_id.
# Only matches hyphen + 4+ digits at end-of-string: never touches real model
# suffixes like -550b-a55b or -k2.6.
_DATE_SUFFIX_RE = re.compile(r"-\d{4,}$")


# ── Tier -> logical model id list ────────────────────────────────────────
# A logical id matches a provider's ModelInfo.id. Same logical id may appear
# under multiple providers (that is the whole point of fallback pools).
TIERS: dict[str, list[str]] = {
    # High: flagship class / 1M context (or the strongest tier of each family)
    "high": [
        "glm-5.2",
        "deepseek-v4-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
    ],
    # Mid: strong models, ~256k-512k context or solid coding/reasoning
    "mid": [
        "minimax-m3",
        "step-3.7-flash",
        "laguna-s-2.1",
        "laguna-xs-2.1",
        "mimo-v2.5",  # matches "mimo-v2.5-free" via _normalize() suffix stripping
        # "ling-3.0-flash" was removed: no provider in the registry actually
        # offers this model, so its tier entry was a silent no-op (zero pool
        # instances). Re-add it to TIERS["mid"] when/if a provider ships it.
        "deepseek-v4-pro",
        "nemotron-3-ultra-550b-a55b",
    ],
    # Low: everything else (small / limited-context / fallback)
    "low": [],  # filled on demand from "all minus high/mid" -- see low_members()
}

# Tier ids exposed to agents (the virtual model names)
TIER_IDS = ("tier/high", "tier/mid", "tier/low")

# Cross-tier cascade: when a requested tier can't serve (every instance
# failed or is in cooldown), spill into the next-lower tier(s) for the
# SAME request, picking fresh instances. Strictly downward only -- a
# `tier/low` request never escalates to `tier/high` -- so the user's
# tier choice is always respected as a *ceiling*, and we only ever fall
# back to something weaker/cheaper, never stronger. The requested tier
# is always first in each list so its preferred instances are tried
# before any lower-tier instance is reached.
_TIER_CASCADE: dict[str, list[str]] = {
    "high": ["high", "mid", "low"],
    "mid": ["mid", "low"],
    "low": ["low"],
}

# Sensible fallback ordering within a tier: we want the most-capable /
# widest-context instance first so the user lands on the best available.
#
# Keyed by (logical_id, provider_name) -> priority (lower = more
# preferred). Anything not listed here gets the default fallback value
# in _expand_logical() and is ordered purely by the context_window
# tiebreaker in tier_members().
#
# FIXED 2026-08-0x: this was previously a `set` of (logical_id,
# provider_name) tuples, looked up in _expand_logical() by destructuring
# each tuple as `(p, uid)` and comparing `p == provider.name` /
# `uid == model.effective_upstream_id`. That's backwards from how the
# tuples were actually populated (first element is the logical id, not
# a provider name; second is the provider name, not an upstream_id) --
# every lookup silently fell through to the "not listed" default,
# meaning this preference table never actually influenced ordering.
# ordering happened to look right anyway (e.g. sensenova's glm-5.2
# sorting before nvidia-nim's) purely by coincidence of the
# context_window tiebreaker in tier_members(), which masked the bug --
# it would have picked the wrong instance for any logical id where the
# "preferred" one isn't also the largest-context one. Also switched
# from a `set` to a `dict`: enumerate() over a set doesn't have a
# stable, meaningful order to assign indices from in the first place.
_INSTANCE_PRIORITY: dict[tuple[str, str], int] = {
    ("glm-5.2", "sensenova"): 0,                       # 1M context
    ("glm-5.2", "nvidia-nim"): 1,
    ("deepseek-v4-flash", "sensenova"): 0,
    ("deepseek-v4-flash", "nvidia-nim"): 1,
    ("deepseek-v4-flash", "opencode-zen-free"): 2,
    ("deepseek-v4-flash", "teamorouter"): 3,
    ("deepseek-v4-pro", "teamorouter"): 0,
    ("nemotron-3-ultra-550b-a55b", "openrouter"): 0,   # 1M
    ("nemotron-3-ultra-550b-a55b", "nvidia-nim"): 1,   # 1M (nemotron reasoning)
    ("nemotron-3-ultra-550b-a55b", "opencode-zen-free"): 2,
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

    - Strips a leading ``provider/`` prefix so that e.g.
      ``minimaxai/minimax-m3`` matches the logical id ``minimax-m3``.
    - Strips ``:free`` and ``-free`` suffixes so that free-tier variants
      like ``stepfun/step-3.7-flash:free`` (Nous) or
      ``deepseek-v4-flash-free`` (OpenCode Zen) are recognized as the same
      logical model as ``step-3.7-flash`` and ``deepseek-v4-flash``. Without
      this, 6 valid cross-provider failover candidates were silently
      excluded from their tiers and only reachable (out of order) under
      the low catch-all -- see audit finding on tier classification.
    """
    if "/" in uid:
        uid = uid.rsplit("/", 1)[-1]
    # All models in this router are free, so the :free / -free markers are
    # just provider convention noise, not part of the canonical name.
    uid = uid.removesuffix(":free").removesuffix("-free")
    # Strip trailing date suffixes so dated builds (e.g. -0731) match the
    # canonical logical id (e.g. deepseek-v4-flash).
    uid = _DATE_SUFFIX_RE.sub("", uid)
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
            if model.id != logical_id and _normalize(uid) != logical_id:
                continue
            priority = _INSTANCE_PRIORITY.get((logical_id, provider.name), 1000)
            found.append((priority, f"{provider.name}/{model.effective_upstream_id}", provider, model))
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
        # Use the same matching logic as _expand_logical: a model is "claimed"
        # if its id OR its normalized effective_upstream_id matches a high/mid logical_id.
        claimed_keys = set()
        for lid in TIERS["high"] + TIERS["mid"]:
            for _prio, key, _prov, _mod in _expand_logical(lid, registry):
                claimed_keys.add(key)  # key is "provider/upstream_id"
        logical_ids = []
        for p in registry.providers.values():
            for m in p.models:
                inst = UpstreamInstance.for_provider(p, m)
                if inst.key not in claimed_keys and inst.key not in logical_ids:
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


def tier_cascade_pool(tier: str, registry: Registry, request_context: int = 0) -> list[UpstreamInstance]:
    """Ordered candidate pool for `tier`, cascading down into lower tiers
    when the requested tier alone can't serve.

    The cascade rank is the primary sort key, so the requested tier's
    instances (already ordered by priority then context_window inside
    tier_members()) are always tried first; only when all of them fail
    or are cooling down do we reach the next tier down. Instances are
    de-duplicated by key so the same (provider, model) deployment is
    never attempted twice within one request.

    This is what makes free-model rate-limit/quota exhaustion invisible
    to the calling application: a `tier/high` request that would
    otherwise 429 because every high-tier instance is rate-limited is
    transparently served by a mid/low instance instead, with the client
    receiving a normal 200 (and, on the buffered path, a rewritten
    `model` field naming the instance that actually answered).
    """
    tiers_to_try = _TIER_CASCADE.get(tier, [tier])
    seen: set[str] = set()
    combined: list[UpstreamInstance] = []
    for t in tiers_to_try:
        for inst in tier_members(t, registry, request_context=request_context):
            if inst.key in seen:
                continue
            seen.add(inst.key)
            combined.append(inst)
    return combined


def tier_filtered_instances(tier: str, registry: Registry,
                            request_context: int = 0) -> list[UpstreamInstance]:
    """Instances in tier's full pool that were *filtered out* by the
    context-window pre-check (context_window < request_context).

    The hot path (tier_members/tier_cascade_pool) just skips these; this
    exists so observability code can tell the caller what was excluded
    and why (per-request trail: X-OFR-Filtered header, x_ofr body).
    Returns [] when request_context is 0/None (no filtering happened).
    """
    if not request_context:
        return []
    full = tier_members(tier, registry, request_context=0)
    kept = tier_members(tier, registry, request_context=request_context)
    kept_keys = {i.key for i in kept}
    return [i for i in full if i.key not in kept_keys]


def is_tier_id(model: str) -> bool:
    return model in TIER_IDS
