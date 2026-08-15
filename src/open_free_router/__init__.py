"""open-free-router — Free LLM Model Router & Pipeline

Quick start:
    pip install open-free-router
    open-free-router serve   # starts proxy (8337) + UI (9057) + scheduler (12h refresh)
    open-free-router ui      # standalone web dashboard
    open-free-router refresh # one-time free model list refresh
    open-free-router add     # add a provider
"""

# Single source of truth for the CLI --version flag and diagnostics.
# Bump in lockstep with CHANGELOG.md. pyproject.toml keeps the same value.
__version__ = "0.2.0"
