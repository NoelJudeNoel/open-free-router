"""Tier-based routing: map a virtual tier ID to a sorted pool of upstream
instances, with context-window pre-filtering.

Design: every model in the registry is classified into exactly one tier by
an ordered family-rule table (``TIER_RULES``), with a context-window
fallback for models no rule covers. This replaced the old hand-maintained
``TIERS`` logical-id lists, which silently rotted: a new model never
entered any tier, a delisted one left a dead entry that matched nothing,
and the low catch-all contained duplicate instance keys.

Ordering inside a tier is:

    family rank (rule preference) -> provider priority -> context window

so the strongest family of a tier is preferred, then the more reliable
provider for that family, then the widest context window as a tiebreaker.

---

Suffix normalisation: ``_normalize()`` strips provider prefixes and the
``:free`` / ``-free`` markers, so ``stepfun/step-3.7-flash:free`` (Nous) and
``deepseek-v4-flash-free`` (OpenCode Zen) are recognised as the same model
family as ``step-3.7-flash`` and ``deepseek-v4-flash``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .registry import ModelInfo, ProviderConfig, Registry


# Strip trailing -MMDD / -YYYYMMDD date suffixes (e.g. deepseek-v4-flash-0731
# -> deepseek-v4-flash) so dated builds match their family rule.
# Only matches hyphen + 4+ digits at end-of-string: never touches real model
# suffixes like -550b-a55b or -k2.6.
_DATE_SUFFIX_RE = re.compile(r"-\d{4,}$")

# Tier ids exposed to agents (the virtual model names)
TIER_IDS = ("tier/high", "tier/mid", "tier/low")
TIER_NAMES = ("high", "mid", "low")


# ── Family rules ────────────────────────────────────────────────────────
# Ordered: FIRST match wins, so put the most specific patterns first.
# Each entry is (compiled regex on the normalized lowercase id, tier, rank).
# Lower rank = stronger family within its tier (used as the primary sort
# key). A model no rule matches falls back to context-window classification
# in classify_model().
TIER_RULES: list[tuple[re.Pattern[str], str, int]] = [
    # -- low: small / narrow models, including sub-variants of families
    #    that otherwise classify as mid (order matters).
    (re.compile(r"^gemma-"), "low", 0),
    (re.compile(r"^diffusiongemma-"), "low", 0),
    (re.compile(r"^muse-glimmer"), "low", 0),
    (re.compile(r"^xing4"), "low", 0),
    (re.compile(r"^lfm-"), "low", 0),
    (re.compile(r"^nemotron-3-nano"), "low", 0),
    (re.compile(r"^nemotron-3\.5-bolt"), "low", 0),

    # -- high: flagship class, 1M context or the strongest tier of a family
    (re.compile(r"^glm-5\.3"), "high", 0),
    (re.compile(r"^deepseek-v4-pro"), "high", 0),
    (re.compile(r"^kimi-k3"), "high", 0),
    (re.compile(r"^gemini-3\.[67]-flash$"), "high", 0),
    (re.compile(r"^glm-5\.2"), "high", 1),
    (re.compile(r"^deepseek-v4\.1"), "high", 1),
    (re.compile(r"^gemini-3\.6-flash$"), "high", 1),
    (re.compile(r"^nemotron-3-ultra"), "high", 1),
    (re.compile(r"^mistral-large"), "high", 1),
    (re.compile(r"^deepseek-v4-flash"), "high", 2),
    (re.compile(r"^minimax-m3"), "high", 2),
    (re.compile(r"^step-5"), "high", 2),

    # -- mid: strong generalists, 128k-512k context
    (re.compile(r"^gemini-3\.5-flash-lite"), "mid", 0),
    (re.compile(r"^sensenova-"), "mid", 0),
    (re.compile(r"^deepseek-flash$"), "mid", 0),
    (re.compile(r"^qwen3\.8"), "mid", 0),
    (re.compile(r"^gpt-oss-120b"), "mid", 0),
    (re.compile(r"^nemotron-3\.5-super"), "mid", 0),
    (re.compile(r"^nemotron-3\.5"), "mid", 1),
    (re.compile(r"^nemotron-3-super"), "mid", 1),
    (re.compile(r"^ling-3"), "mid", 1),
    (re.compile(r"^mimo-v2"), "mid", 1),
    (re.compile(r"^laguna-s"), "mid", 1),
    (re.compile(r"^laguna-"), "mid", 2),
    (re.compile(r"^gpt-oss-"), "mid", 2),
    (re.compile(r"^ornith-"), "mid", 2),
    (re.compile(r"^dots-3"), "mid", 2),
    (re.compile(r"^north-mini"), "mid", 2),
    (re.compile(r"^flint"), "mid", 2),
    (re.compile(r"^big-pickle"), "mid", 2),
    (re.compile(r"^beam(-|$)"), "mid", 2),
]

# Provider preference within a family (lower = tried first). Reflects
# reliability + context as observed in production; unknown providers sort
# last so a manually added provider never outranks the curated ones.
PROVIDER_PRIORITY: dict[str, int] = {
    "sensenova": 0,
    "nvidia-nim": 1,
    "openrouter": 2,
    "google-ai-studio": 3,
    "opencode-zen-free": 4,
    "nous": 5,
    "poolside": 6,
    "leuai": 7,
    "groq": 8,
    "bai": 10,
}
_DEFAULT_PROVIDER_PRIORITY = 50

# Cross-tier cascade: when a requested tier can't serve (every instance
# failed or is in cooldown), spill into the next-lower tier(s) for the
# SAME request, picking fresh instances. Strictly downward only -- a
# `tier/low` request never escalates to `tier/high` -- so the user's
# tier choice is always respected as a *ceiling*, and we only ever fall
# back to something weaker/cheaper, never stronger.
_TIER_CASCADE: dict[str, list[str]] = {
    "high": ["high", "mid", "low"],
    "mid": ["mid", "low"],
    "low": ["low"],
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
      ``minimaxai/minimax-m3`` matches the family rule for ``minimax-m3``.
    - Strips ``:free`` and ``-free`` suffixes so that free-tier variants
      like ``stepfun/step-3.7-flash:free`` (Nous) or
      ``deepseek-v4-flash-free`` (OpenCode Zen) are recognised as the same
      family as ``step-3.7-flash`` and ``deepseek-v4-flash``.
    - Strips trailing date suffixes so dated builds (e.g. ``-0731``) match
      the canonical family name.
    """
    if "/" in uid:
        uid = uid.rsplit("/", 1)[-1]
    # All models in this router are free, so the :free / -free markers are
    # just provider convention noise, not part of the canonical name.
    uid = uid.removesuffix(":free").removesuffix("-free")
    uid = _DATE_SUFFIX_RE.sub("", uid)
    return uid


def classify_model(upstream_id: str, context_window: int = 0,
                   reasoning: bool = False) -> str:
    """Return the tier name for a model.

    Family rules win over the context fallback: an explicit small-family
    rule (e.g. ``nemotron-3.5-bolt``) must not be dragged up to mid just
    because the provider reports a large context window. Uncovered models
    fall back to: >=500k context with reasoning -> high, >=128k -> mid,
    everything else -> low.
    """
    return _classify(upstream_id, context_window, reasoning)[0]


def _classify(upstream_id: str, context_window: int, reasoning: bool) -> tuple[str, int]:
    name = _normalize(upstream_id).lower()
    for pattern, tier, rank in TIER_RULES:
        if pattern.search(name):
            return tier, rank
    if context_window >= 500_000 and reasoning:
        return "high", 9
    if context_window >= 128_000:
        return "mid", 9
    return "low", 9


def _provider_priority(provider_name: str) -> int:
    return PROVIDER_PRIORITY.get(provider_name, _DEFAULT_PROVIDER_PRIORITY)


def tier_members(tier: str, registry: Registry, request_context: int = 0) -> list[UpstreamInstance]:
    """Return the ordered candidate pool for a tier.

    - tier is one of TIER_NAMES
    - request_context (tokens) filters out instances whose context_window
      is too small (context_window_fallbacks pre-check)
    - ordered by family rank, provider priority, then widest context
    - de-duplicated by instance key (provider/upstream_id)
    """
    candidates: list[tuple[int, int, int, str, UpstreamInstance]] = []
    seen: set[str] = set()
    for provider in registry.providers.values():
        for model in provider.models:
            model_tier, rank = _classify(
                model.effective_upstream_id, model.context_window, model.reasoning)
            if model_tier != tier:
                continue
            if request_context and model.context_window < request_context:
                continue
            inst = UpstreamInstance.for_provider(provider, model)
            if inst.key in seen:
                continue
            seen.add(inst.key)
            candidates.append((
                rank,
                _provider_priority(provider.name),
                -inst.context_window,
                inst.key,
                inst,
            ))
    candidates.sort(key=lambda t: t[:4])
    return [inst for *_, inst in candidates]


def tier_cascade_pool(tier: str, registry: Registry, request_context: int = 0) -> list[UpstreamInstance]:
    """Ordered candidate pool for `tier`, cascading down into lower tiers
    when the requested tier alone can't serve.

    The cascade rank is the primary sort key, so the requested tier's
    instances (already ordered inside tier_members()) are always tried
    first; only when all of them fail or are cooling down do we reach the
    next tier down. Instances are de-duplicated by key so the same
    (provider, model) deployment is never attempted twice within one
    request.

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
