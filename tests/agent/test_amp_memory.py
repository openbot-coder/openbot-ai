"""Tests for AMP v1.0 memory alignment features."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from openbot.agent.memory import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    ms = MemoryStore(tmp_path)
    (tmp_path / "memory").mkdir(exist_ok=True)
    (tmp_path / "memory" / "knowledge").mkdir(exist_ok=True)
    (tmp_path / "memory" / "session").mkdir(exist_ok=True)
    (tmp_path / "memory" / "promotion_candidates").mkdir(exist_ok=True)
    (tmp_path / "memory" / "archive").mkdir(exist_ok=True)
    return ms


class TestWriteAmpMemory:
    def test_writes_file_with_frontmatter(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "test.md"
        store.write_amp_memory(
            target,
            {"type": "Fact", "scope": "project", "confidence": "confirmed"},
            "Test body content",
        )
        content = target.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "type: Fact" in content
        assert "scope: project" in content
        assert "Test body content" in content

    def test_creates_parent_directories(self, store: MemoryStore):
        target = store.knowledge_dir / "decisions" / "nested" / "test.md"
        store.write_amp_memory(target, {"type": "Decision"}, "body")
        assert target.exists()

    def test_overwrites_existing(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "test.md"
        store.write_amp_memory(target, {"type": "Fact"}, "first")
        store.write_amp_memory(target, {"type": "Fact"}, "second")
        content = target.read_text(encoding="utf-8")
        assert "second" in content
        assert "first" not in content


class TestReadAmpMemory:
    def test_reads_frontmatter_and_body(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "test.md"
        store.write_amp_memory(
            target,
            {"type": "Fact", "tags": ["a", "b"]},
            "Body text here",
        )
        parsed = store.read_amp_memory(target)
        assert parsed["frontmatter"]["type"] == "Fact"
        assert parsed["frontmatter"]["tags"] == ["a", "b"]
        assert parsed["body"] == "Body text here"

    def test_returns_body_only_when_no_frontmatter(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "plain.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("plain body", encoding="utf-8")
        parsed = store.read_amp_memory(target)
        assert parsed["frontmatter"] == {}
        assert parsed["body"] == "plain body"

    def test_returns_none_for_missing_file(self, store: MemoryStore):
        parsed = store.read_amp_memory(store.knowledge_dir / "nonexistent.md")
        assert parsed is None


class TestUpdateActivation:
    def test_increments_activation_count(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "test.md"
        store.write_amp_memory(
            target,
            {"type": "Fact", "activation_count": 0},
            "body",
        )
        store.update_activation(target)
        parsed = store.read_amp_memory(target)
        assert parsed["frontmatter"]["activation_count"] == 1

    def test_updates_last_activated(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "test.md"
        store.write_amp_memory(
            target,
            {"type": "Fact"},
            "body",
        )
        before = datetime.utcnow()
        store.update_activation(target)
        parsed = store.read_amp_memory(target)
        last_activated = parsed["frontmatter"]["last_activated"]
        assert last_activated
        parsed_time = datetime.fromisoformat(last_activated.replace("Z", "+00:00"))
        assert parsed_time.replace(tzinfo=None) >= before - timedelta(seconds=5)

    def test_handles_missing_activation_count(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "test.md"
        store.write_amp_memory(
            target,
            {"type": "Fact"},
            "body",
        )
        store.update_activation(target)
        parsed = store.read_amp_memory(target)
        assert parsed["frontmatter"]["activation_count"] == 1


class TestLintMemories:
    def test_detects_stale_weak_memory(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "old.md"
        old_date = (datetime.utcnow() - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
        store.write_amp_memory(
            target,
            {
                "type": "Fact",
                "strength": "weak",
                "last_activated": old_date,
                "activation_count": 0,
            },
            "stale content",
        )
        issues = store.lint_memories()
        stale_issues = [i for i in issues if i["type"] == "stale"]
        assert len(stale_issues) == 1
        assert stale_issues[0]["path"].endswith("old.md")

    def test_ignores_recent_weak_memory(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "recent.md"
        store.write_amp_memory(
            target,
            {"type": "Fact", "strength": "weak", "activation_count": 1},
            "recent content",
        )
        issues = store.lint_memories()
        stale_issues = [i for i in issues if i["type"] == "stale"]
        assert len(stale_issues) == 0

    def test_detects_orphan_memory(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "orphan.md"
        store.write_amp_memory(
            target,
            {"type": "Fact"},
            "orphan content",
        )
        issues = store.lint_memories()
        orphan_issues = [i for i in issues if i["type"] == "orphan"]
        assert len(orphan_issues) == 1

    def test_ignores_memory_with_tags(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "tagged.md"
        store.write_amp_memory(
            target,
            {"type": "Fact", "tags": ["important"]},
            "tagged content",
        )
        issues = store.lint_memories()
        orphan_issues = [i for i in issues if i["type"] == "orphan"]
        assert len(orphan_issues) == 0

    def test_detects_promotion_candidate(self, store: MemoryStore):
        slug = "test-learning"
        credits_file = store.promotion_dir / slug / "credits.md"
        credits_file.parent.mkdir(parents=True, exist_ok=True)
        credits_file.write_text("2", encoding="utf-8")
        issues = store.lint_memories()
        promo_issues = [i for i in issues if i["type"] == "promotion"]
        assert len(promo_issues) == 1
        assert promo_issues[0]["path"].endswith(slug)


class TestComputeStrength:
    def test_strong_never_activated_decays(self, store: MemoryStore):
        memory = {"strength": "strong", "activation_count": 0}
        strength = store.compute_strength(memory)
        assert 0 < strength < 1.0

    def test_higher_activation_slower_decay(self, store: MemoryStore):
        base = {"strength": "strong", "activation_count": 0, "last_activated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
        high = dict(base, activation_count=10)
        base_strength = store.compute_strength(base)
        high_strength = store.compute_strength(high)
        assert high_strength > base_strength

    def test_weak_base_lower_than_strong(self, store: MemoryStore):
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        weak = {"strength": "weak", "activation_count": 0, "last_activated": now}
        strong = {"strength": "strong", "activation_count": 0, "last_activated": now}
        assert store.compute_strength(weak) < store.compute_strength(strong)


class TestSyncToLegacyMemory:
    def test_sync_appends_summary(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "test.md"
        store.write_amp_memory(
            target,
            {"type": "Fact", "title": "Test Fact", "scope": "project", "tags": ["test"]},
            "Test body content",
        )
        store.sync_to_legacy_memory()
        legacy = store.read_memory()
        assert "Test Fact" in legacy or "Test body content" in legacy

    def test_should_sync_true_when_no_hash(self, store: MemoryStore):
        assert store.should_sync() is True

    def test_should_sync_false_after_sync(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "test.md"
        store.write_amp_memory(target, {"type": "Fact"}, "body")
        store.sync_to_legacy_memory()
        assert store.should_sync() is False

    def test_should_sync_true_after_change(self, store: MemoryStore):
        target = store.knowledge_dir / "facts" / "test.md"
        store.write_amp_memory(target, {"type": "Fact"}, "body v1")
        store.sync_to_legacy_memory()
        assert store.should_sync() is False
        target.write_text("changed", encoding="utf-8")
        assert store.should_sync() is True
