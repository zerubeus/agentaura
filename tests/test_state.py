"""Tests for ImportState and session_checksum."""

from pathlib import Path

from agentaura.pipeline.state import ImportState, session_checksum


def test_unknown_session_not_imported(tmp_path: Path):
    state = ImportState(db_path=tmp_path / "test.db")
    assert state.is_imported("nonexistent", "abc123") is False
    state.close()


def test_mark_and_check_imported(tmp_path: Path):
    db = tmp_path / "test.db"
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text('{"type":"user"}\n')

    state = ImportState(db_path=db)
    cs = session_checksum(jsonl)
    state.mark_imported("s1", jsonl, cs, event_count=10, cost_usd=1.5)
    assert state.is_imported("s1", cs) is True
    state.close()


def test_reimport_after_file_change(tmp_path: Path):
    db = tmp_path / "test.db"
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text('{"type":"user"}\n')

    state = ImportState(db_path=db)
    cs1 = session_checksum(jsonl)
    state.mark_imported("s1", jsonl, cs1, event_count=5, cost_usd=1.0)

    # Modify the file
    jsonl.write_text('{"type":"user"}\n{"type":"assistant"}\n')
    cs2 = session_checksum(jsonl)
    assert cs1 != cs2
    assert state.is_imported("s1", cs2) is False
    state.close()


def test_get_stats(tmp_path: Path):
    db = tmp_path / "test.db"
    state = ImportState(db_path=db)
    jsonl1 = tmp_path / "s1.jsonl"
    jsonl2 = tmp_path / "s2.jsonl"
    jsonl1.write_text("a\n")
    jsonl2.write_text("b\n")

    state.mark_imported("s1", jsonl1, "cs1", event_count=10, cost_usd=5.0)
    state.mark_imported("s2", jsonl2, "cs2", event_count=20, cost_usd=3.0)

    stats = state.get_stats()
    assert stats["imported_sessions"] == 2
    assert stats["total_events"] == 30
    assert abs(stats["total_cost_usd"] - 8.0) < 0.001
    state.close()


def test_checksum_includes_subagent_files(tmp_path: Path):
    """Checksum changes when subagent files change."""
    proj = tmp_path / "projects" / "test-proj"
    proj.mkdir(parents=True)
    jsonl = proj / "session-001.jsonl"
    jsonl.write_text('{"type":"user"}\n')

    # No subagents
    cs1 = session_checksum(jsonl)

    # Add subagent dir
    sa_dir = proj / "session-001" / "subagents"
    sa_dir.mkdir(parents=True)
    (sa_dir / "agent-abc.jsonl").write_text('{"type":"user"}\n')

    cs2 = session_checksum(jsonl)
    assert cs1 != cs2


def test_checksum_includes_tool_results(tmp_path: Path):
    """Checksum changes when tool-result sidecar files change."""
    proj = tmp_path / "projects" / "test-proj"
    proj.mkdir(parents=True)
    jsonl = proj / "session-001.jsonl"
    jsonl.write_text('{"type":"user"}\n')

    cs1 = session_checksum(jsonl)

    # Add tool-results dir
    tr_dir = proj / "session-001" / "tool-results"
    tr_dir.mkdir(parents=True)
    (tr_dir / "abc123.txt").write_text("large output here")

    cs2 = session_checksum(jsonl)
    assert cs1 != cs2


def test_checksum_deterministic(tmp_path: Path):
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text('{"type":"user"}\n')
    assert session_checksum(jsonl) == session_checksum(jsonl)


def test_state_scoped_to_claude_dir(tmp_path: Path):
    """ImportState with claude_dir puts DB inside that directory."""
    claude_dir = tmp_path / "custom-claude"
    claude_dir.mkdir()
    state = ImportState(claude_dir=claude_dir)
    state.close()
    assert (claude_dir / "agentaura_import.db").exists()
