"""Tests for the opt-in git-based registry history (registry.py's
git_commit_registry / Registry.save(git_history=True)).
"""
from __future__ import annotations

import shutil
import subprocess
from unittest.mock import patch

import pytest

from open_free_router.registry import ModelInfo, Registry, git_commit_registry

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


class TestGitCommitRegistry:
    def test_off_by_default_no_repo_created(self, tmp_path):
        reg_path = tmp_path / "registry.yaml"
        reg = Registry({"fake": {"upstream_url": "https://example.com/v1", "models": [{"id": "m1"}]}})
        reg.save(reg_path)  # git_history not passed -> default False
        assert not (tmp_path / ".git").exists()

    def test_enabled_creates_repo_and_commits(self, tmp_path):
        reg_path = tmp_path / "registry.yaml"
        reg = Registry({"fake": {"upstream_url": "https://example.com/v1", "models": [{"id": "m1"}]}})
        reg.save(reg_path, git_history=True)

        assert (tmp_path / ".git").exists()
        log = _git(["log", "--oneline"], cwd=tmp_path).stdout
        assert "registry.yaml update" in log

    def test_second_save_adds_a_second_commit(self, tmp_path):
        reg_path = tmp_path / "registry.yaml"
        reg = Registry({"fake": {"upstream_url": "https://example.com/v1", "models": [{"id": "m1"}]}})
        reg.save(reg_path, git_history=True)
        reg.update_models("fake", [ModelInfo(id="m2")])
        reg.save(reg_path, git_history=True)

        log = _git(["log", "--oneline"], cwd=tmp_path).stdout.strip().splitlines()
        # init commit (.gitignore) + 2 registry.yaml commits
        assert len(log) == 3

    def test_gitignore_excludes_everything_but_registry_and_itself(self, tmp_path):
        reg_path = tmp_path / "registry.yaml"
        (tmp_path / "ui.token").write_text("super-secret-token")
        reg = Registry({"fake": {"upstream_url": "https://example.com/v1", "models": [{"id": "m1"}]}})
        reg.save(reg_path, git_history=True)

        tracked = _git(["ls-files"], cwd=tmp_path).stdout.strip().splitlines()
        assert "registry.yaml" in tracked
        assert ".gitignore" in tracked
        assert "ui.token" not in tracked

    def test_unchanged_save_does_not_error_on_empty_commit(self, tmp_path):
        reg_path = tmp_path / "registry.yaml"
        reg = Registry({"fake": {"upstream_url": "https://example.com/v1", "models": [{"id": "m1"}]}})
        reg.save(reg_path, git_history=True)
        reg.save(reg_path, git_history=True)  # identical content -> no-op commit, must not raise

    def test_missing_git_binary_is_a_silent_noop(self, tmp_path):
        reg_path = tmp_path / "registry.yaml"
        reg_path.write_text("providers: {}\n")
        with patch("shutil.which", return_value=None):
            git_commit_registry(reg_path)  # must not raise
        assert not (tmp_path / ".git").exists()

    def test_reuses_existing_repo_does_not_reinit(self, tmp_path):
        reg_path = tmp_path / "registry.yaml"
        reg = Registry({"fake": {"upstream_url": "https://example.com/v1", "models": [{"id": "m1"}]}})
        reg.save(reg_path, git_history=True)
        first_log = _git(["log", "--oneline"], cwd=tmp_path).stdout

        reg.save(reg_path, git_history=True)
        second_log = _git(["log", "--oneline"], cwd=tmp_path).stdout
        # no duplicate "init history repo" commit
        assert second_log.count("init history repo") == 1
        assert first_log.count("init history repo") == 1
