"""Tests for Codex CLI adapter — parser, normalizer, and adapter."""

from pathlib import Path

from agentaura.adapters.codex.adapter import CodexAdapter
from agentaura.adapters.codex.normalizer import normalize_codex_session
from agentaura.adapters.codex.parser import parse_codex_session

FIXTURES = Path(__file__).parent / "fixtures"
CODEX_SESSION = FIXTURES / "codex_session.jsonl"


# --- Parser ---


def test_codex_parser():
    parsed = parse_codex_session(CODEX_SESSION)
    assert parsed.session_id == "codex-test-001"
    assert len(parsed.events) > 0


def test_codex_meta():
    parsed = parse_codex_session(CODEX_SESSION)
    assert parsed.meta["cwd"] == "/home/user/webapp"
    assert parsed.meta["cli_version"] == "0.116.0"
    assert parsed.meta["source"] == "cli"
    assert parsed.meta["git"]["branch"] == "main"


def test_codex_event_types():
    parsed = parse_codex_session(CODEX_SESSION)
    types = {e.type for e in parsed.events}
    assert "session_meta" in types
    assert "response_item" in types
    assert "event_msg" in types
    assert "turn_context" in types


# --- Normalizer ---


def _get_codex():
    parsed = parse_codex_session(CODEX_SESSION)
    return normalize_codex_session(parsed)


def test_codex_session_metadata():
    ns = _get_codex()
    assert ns.session_id == "codex-test-001"
    assert ns.cwd == "/home/user/webapp"
    assert ns.git_branch == "main"
    assert ns.model == "gpt-5.4"
    assert ns.version == "0.116.0"
    assert ns.entrypoint == "cli"


def test_codex_two_turns():
    ns = _get_codex()
    assert len(ns.turns) == 2


def test_codex_turn1_prompt():
    ns = _get_codex()
    assert "Fix the broken API endpoint" in ns.turns[0].user_prompt


def test_codex_turn2_prompt():
    ns = _get_codex()
    assert "add unit tests" in ns.turns[1].user_prompt


def test_codex_turn1_tool_calls():
    ns = _get_codex()
    all_tcs = [tc for g in ns.turns[0].generations for tc in g.tool_calls]
    names = {tc.name for tc in all_tcs}
    assert "exec_command" in names
    assert "apply_diff" in names


def test_codex_tool_call_output():
    ns = _get_codex()
    all_tcs = [tc for g in ns.turns[0].generations for tc in g.tool_calls]
    exec_tc = next(tc for tc in all_tcs if tc.name == "exec_command")
    assert "Missing email validation" in exec_tc.output_content


def test_codex_turn1_assistant_response():
    ns = _get_codex()
    gens = ns.turns[0].generations
    text_gen = next((g for g in gens if g.text_content), None)
    assert text_gen is not None
    assert "email validation" in text_gen.text_content


def test_codex_turn2_tool_call():
    ns = _get_codex()
    all_tcs = [tc for g in ns.turns[1].generations for tc in g.tool_calls]
    assert len(all_tcs) >= 1
    assert all_tcs[0].name == "write_file"


def test_codex_thinking_detected():
    ns = _get_codex()
    # Reasoning block should set has_thinking on subsequent generation
    # The reasoning appears before the first generation
    gens = ns.turns[0].generations
    assert any(g.has_thinking for g in gens)


def test_codex_token_totals():
    ns = _get_codex()
    assert ns.total_input_tokens > 0
    assert ns.total_output_tokens > 0


def test_codex_generation_count():
    ns = _get_codex()
    assert ns.total_generations >= 2
    assert ns.total_tool_calls >= 3


# --- Adapter ---


def test_codex_adapter():
    adapter = CodexAdapter()
    assert adapter.agent_name == "Codex"


def test_codex_adapter_discover(tmp_path: Path):
    # Create fake codex sessions dir
    sessions_dir = tmp_path / "sessions" / "2026" / "03" / "15"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "rollout-2026-03-15T10-00-00-test.jsonl").write_text(
        '{"type":"session_meta","payload":{"id":"test"},"timestamp":"2026-03-15T10:00:00Z"}\n'
    )

    sessions = adapter_discover(tmp_path)
    assert len(sessions) == 1
    assert sessions[0].name.startswith("rollout-")


def adapter_discover(codex_dir: Path) -> list[Path]:
    from agentaura.adapters.codex.parser import discover_codex_sessions

    return discover_codex_sessions(codex_dir)
