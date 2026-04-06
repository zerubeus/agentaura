"""Tests for JSONL parser."""

from pathlib import Path

from agentaura.adapters.claude_code.parser import discover_sessions, parse_session

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_sample_session():
    session = parse_session(FIXTURES / "sample_session.jsonl", project_path="test-project")

    assert session.session_id == "sample_session"
    assert session.project_path == "test-project"
    assert len(session.events) > 0


def test_event_types_parsed():
    session = parse_session(FIXTURES / "sample_session.jsonl")
    types = {type(e).__name__ for e in session.events}

    assert "UserEvent" in types
    assert "AssistantEvent" in types
    assert "SystemEvent" in types
    assert "PermissionModeEvent" in types
    assert "FileHistorySnapshotEvent" in types
    assert "LastPromptEvent" in types


def test_event_count():
    session = parse_session(FIXTURES / "sample_session.jsonl")
    # 1 permission-mode + 4 user + 1 file-history + 4 assistant + 2 system + 1 last-prompt = 13
    assert len(session.events) == 13


def test_assistant_events_have_usage():
    from agentaura.core.events import AssistantEvent

    session = parse_session(FIXTURES / "sample_session.jsonl")
    assistant_events = [e for e in session.events if isinstance(e, AssistantEvent)]

    assert len(assistant_events) == 4
    for ae in assistant_events:
        assert ae.message is not None
        assert ae.message.usage is not None
        assert ae.message.model == "claude-opus-4-6"


def test_tool_use_blocks():
    from agentaura.core.events import AssistantEvent, ToolUseBlock

    session = parse_session(FIXTURES / "sample_session.jsonl")
    assistant_events = [e for e in session.events if isinstance(e, AssistantEvent)]

    # First assistant has 1 tool call (Read)
    a1 = assistant_events[0]
    assert a1.message is not None
    tool_uses = [b for b in a1.message.content if isinstance(b, ToolUseBlock)]
    assert len(tool_uses) == 1
    assert tool_uses[0].name == "Read"

    # Third assistant has 2 parallel tool calls (Bash x2)
    a3 = assistant_events[2]
    assert a3.message is not None
    tool_uses = [b for b in a3.message.content if isinstance(b, ToolUseBlock)]
    assert len(tool_uses) == 2
    assert tool_uses[0].name == "Bash"
    assert tool_uses[1].name == "Bash"


def test_discover_sessions_returns_paths(tmp_path: Path):
    # Set up a fake ~/.claude/projects structure
    proj = tmp_path / "projects" / "-test-project"
    proj.mkdir(parents=True)
    minimal = '{"type":"permission-mode","permissionMode":"default","sessionId":"s1"}\n'
    (proj / "session-001.jsonl").write_text(minimal)
    (proj / "session-002.jsonl").write_text(minimal)
    # Subagent JSONL should be excluded
    sub = proj / "session-001" / "subagents"
    sub.mkdir(parents=True)
    sub_line = '{"type":"user","message":{"role":"user","content":"hi"}}\n'
    (sub / "agent-abc.jsonl").write_text(sub_line)

    sessions = discover_sessions(claude_dir=tmp_path)
    assert len(sessions) == 2
    for s in sessions:
        assert s.suffix == ".jsonl"
        assert "subagents" not in s.parts
