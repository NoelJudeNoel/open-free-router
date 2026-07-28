"""Basic tests for open-free-router."""
import pytest
from pathlib import Path

from open_free_router.registry import Registry, ModelInfo, ProviderConfig


@pytest.fixture
def tmp_registry_path(tmp_path):
    return tmp_path / "registry.yaml"


@pytest.fixture
def sample_registry():
    return Registry({
        "openrouter": {
            "upstream_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-test",
            "auto_refresh": True,
            "refresh_method": "openrouter_api",
            "models": [
                {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "reasoning": True},
                {"id": "inclusionai/ling-3.0-flash:free"},
            ],
        },
        "deepseek": {
            "upstream_url": "https://api.deepseek.com/v1",
            "api_key": "sk-ds",
            "models": [
                {"id": "deepseek-chat"},
                {"id": "deepseek-reasoner", "reasoning": True},
            ],
        },
    })


class TestModelInfo:
    def test_defaults(self):
        m = ModelInfo(id="test-model")
        assert m.name == ""
        assert m.context_window == 131072
        assert m.max_tokens == 8192
        assert m.reasoning is False

    def test_from_dict(self):
        m = ModelInfo.from_dict({"id": "m1", "name": "Model 1", "reasoning": True})
        assert m.id == "m1"
        assert m.name == "Model 1"
        assert m.reasoning is True

    def test_to_dict_omits_defaults(self):
        m = ModelInfo(id="m1")
        d = m.to_dict()
        assert "context_window" not in d
        assert "max_tokens" not in d
        assert "reasoning" not in d
        assert d == {"id": "m1"}

    def test_to_dict_includes_non_defaults(self):
        m = ModelInfo(id="m1", name="M1", context_window=64000, max_tokens=4096, reasoning=True)
        d = m.to_dict()
        assert d == {"id": "m1", "name": "M1", "context_window": 64000, "max_tokens": 4096, "reasoning": True}

    def test_upstream_id_fallback(self):
        m = ModelInfo(id="glm-5.2")
        assert m.effective_upstream_id == "glm-5.2"

    def test_upstream_id_explicit(self):
        m = ModelInfo(id="glm-5.2", upstream_id="z-ai/glm-5.2")
        assert m.effective_upstream_id == "z-ai/glm-5.2"

    def test_to_dict_includes_upstream_id(self):
        m = ModelInfo(id="glm-5.2", upstream_id="z-ai/glm-5.2")
        d = m.to_dict()
        assert d["id"] == "glm-5.2"
        assert d["upstream_id"] == "z-ai/glm-5.2"


class TestProviderConfig:
    def test_effective_key_single(self):
        p = ProviderConfig(name="test", api_key="sk-abc")
        assert p.effective_key == "sk-abc"

    def test_effective_key_multi(self):
        p = ProviderConfig(name="test", api_key="sk-old", api_keys=["sk-new1", "sk-new2"])
        assert p.effective_key == "sk-new1"

    def test_free_model_ids(self):
        p = ProviderConfig(
            name="test",
            models=[ModelInfo(id="m1"), ModelInfo(id="m2")],
        )
        assert p.free_model_ids() == {"m1", "m2"}


class TestRegistry:
    def test_load_and_get(self, sample_registry):
        p = sample_registry.get("openrouter")
        assert p is not None
        assert p.api_key == "sk-test"
        assert len(p.models) == 2
        assert p.auto_refresh is True

    def test_get_missing(self, sample_registry):
        assert sample_registry.get("nonexistent") is None

    def test_update_models(self, sample_registry):
        new_models = [ModelInfo(id="new-model")]
        assert sample_registry.update_models("openrouter", new_models)
        assert len(sample_registry.get("openrouter").models) == 1
        assert sample_registry.get("openrouter").models[0].id == "new-model"

    def test_update_models_missing_provider(self, sample_registry):
        assert not sample_registry.update_models("nope", [])

    def test_add_provider(self, sample_registry):
        p = ProviderConfig(name="groq", upstream_url="https://api.groq.com/openai/v1", models=[ModelInfo(id="llama-3.3-70b")])
        sample_registry.add_provider(p)
        assert sample_registry.get("groq") is not None

    def test_save_and_reload(self, sample_registry, tmp_registry_path):
        sample_registry.save(tmp_registry_path)
        assert tmp_registry_path.exists()
        loaded = Registry.load(tmp_registry_path)
        assert len(loaded.providers) == 2
        assert loaded.get("openrouter").api_key == "sk-test"
        assert loaded.get("deepseek").models[1].id == "deepseek-reasoner"

    def test_load_missing_file(self, tmp_path):
        loaded = Registry.load(tmp_path / "nonexistent.yaml")
        assert len(loaded.providers) == 0

    def test_to_dict_roundtrip(self, sample_registry, tmp_registry_path):
        sample_registry.save(tmp_registry_path)
        loaded = Registry.load(tmp_registry_path)
        loaded.save(tmp_registry_path)
        loaded2 = Registry.load(tmp_registry_path)
        assert len(loaded2.providers) == len(sample_registry.providers)


class TestProxyHandler:
    def test_rebuild_index(self):
        from open_free_router.proxy import _ProxyHandler
        reg = Registry({
            "openrouter": {
                "upstream_url": "https://openrouter.ai/api/v1",
                "models": [{"id": "m1"}, {"id": "m2"}],
            },
            "deepseek": {
                "upstream_url": "https://api.deepseek.com/v1",
                "models": [{"id": "d1"}],
            },
        })
        _ProxyHandler.registry = reg
        _ProxyHandler.rebuild_index()
        assert _ProxyHandler._model_index["m1"] == "openrouter"
        assert _ProxyHandler._model_index["m2"] == "openrouter"
        assert _ProxyHandler._model_index["d1"] == "deepseek"

    def test_rebuild_index_provider_upstream_format(self):
        """provider/upstream_id format must resolve for OMP compatibility."""
        from open_free_router.proxy import _ProxyHandler
        reg = Registry({
            "nvidia-nim": {
                "upstream_url": "https://integrate.api.nvidia.com/v1",
                "prefix": "nv",
                "models": [
                    {"id": "glm-5.2", "upstream_id": "z-ai/glm-5.2"},
                    {"id": "minimax-m3", "upstream_id": "minimaxai/minimax-m3"},
                ],
            },
        })
        _ProxyHandler.registry = reg
        _ProxyHandler.rebuild_index()
        index = _ProxyHandler._model_index
        # New format: provider/upstream_id (OMP uses nvidia-nim/z-ai/glm-5.2)
        assert index["nvidia-nim/z-ai/glm-5.2"] == "nvidia-nim"
        # provider/upstream_id when upstream_id itself contains a slash
        assert index["nvidia-nim/minimaxai/minimax-m3"] == "nvidia-nim"
        # Existing formats still work
        assert index["nv/glm-5.2"] == "nvidia-nim"
        assert index["glm-5.2"] == "nvidia-nim"
        assert index["z-ai/glm-5.2"] == "nvidia-nim"
