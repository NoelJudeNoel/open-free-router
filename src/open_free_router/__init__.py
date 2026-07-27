"""open-free-router — Free LLM Model Router & Sync Engine

A lightweight system that:
- Tracks free models across multiple LLM providers (OpenRouter, NIM, Zen, etc.)
- Runs a local proxy that filters out paid models (ports 8337/8338)
- Auto-syncs the free model list to Hermes, Pi, OMP, OpenCode
- Provides a web dashboard for monitoring and management

Quick start:
    cp config.example.yaml ~/.config/open-free-router/config.yaml
    # Edit config.yaml to add your providers and API keys
    open-free-router proxy   # starts proxy on 8337/8338
    open-free-router ui      # starts web dashboard on 9527
    open-free-router refresh # refresh free model lists
    open-free-router sync    # sync to all agents
"""
