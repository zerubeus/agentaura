"""Tests for session normalization (the mapper from raw events to normalized hierarchy)."""

from pathlib import Path

from agentaura.adapters.claude_code.parser import parse_session
from agentaura.core.normalized import normalize_session

FIXTURES = Path(__file__).parent / "fixtures"


def _get_normalized():
    parsed = parse_session(FIXTURES / "sample_session.jsonl", project_path="test-project")
    return normalize_session(parsed)


def test_session_metadata():
    ns = _get_normalized()
    assert ns.session_id == "sample_session"
    assert ns.project_path == "test-project"
    assert ns.cwd == "/home/user/project"
    assert ns.git_branch == "main"
    assert ns.model == "claude-opus-4-6"
    assert ns.version == "2.1.92"
    assert ns.entrypoint == "cli"
    assert ns.slug == "test-slug"


def test_two_turns():
    ns = _get_normalized()
    assert len(ns.turns) == 2
    assert ns.turns[0].turn_number == 1
    assert ns.turns[1].turn_number == 2


def test_turn_prompts():
    ns = _get_normalized()
    assert "read the README" in ns.turns[0].user_prompt
    assert "run the tests" in ns.turns[1].user_prompt


def test_turn1_generations():
    ns = _get_normalized()
    t1 = ns.turns[0]
    # Turn 1 has 2 generations: first with tool_use, second with end_turn
    assert len(t1.generations) == 2
    assert t1.generations[0].stop_reason == "tool_use"
    assert t1.generations[1].stop_reason == "end_turn"


def test_turn1_tool_calls():
    ns = _get_normalized()
    t1 = ns.turns[0]
    # First generation has 1 tool call (Read)
    assert len(t1.generations[0].tool_calls) == 1
    tc = t1.generations[0].tool_calls[0]
    assert tc.name == "Read"
    assert tc.id == "toolu_01ABC"
    assert "README" in tc.input_params.get("file_path", "")
    assert "My Project" in tc.output_content


def test_turn2_parallel_tool_calls():
    ns = _get_normalized()
    t2 = ns.turns[1]
    # Turn 2 has 2 generations: first with 2 parallel tool_uses, second with end_turn
    assert len(t2.generations) == 2
    gen1 = t2.generations[0]
    assert len(gen1.tool_calls) == 2
    assert gen1.tool_calls[0].name == "Bash"
    assert gen1.tool_calls[1].name == "Bash"
    assert "tests passed" in gen1.tool_calls[0].output_content
    assert "No lint errors" in gen1.tool_calls[1].output_content


def test_thinking_detected():
    ns = _get_normalized()
    # First generation in turn 1 has thinking block
    assert ns.turns[0].generations[0].has_thinking is True
    # Second generation should not
    assert ns.turns[0].generations[1].has_thinking is False


def test_cost_computed():
    ns = _get_normalized()
    assert ns.total_cost_usd > 0
    # All generations should have cost
    for turn in ns.turns:
        for gen in turn.generations:
            assert gen.cost_usd >= 0


def test_token_totals():
    ns = _get_normalized()
    assert ns.total_input_tokens > 0
    assert ns.total_output_tokens > 0
    assert ns.total_generations == 4  # 4 assistant events


def test_total_tool_calls():
    ns = _get_normalized()
    # 1 (Read) + 2 (Bash x2) = 3 tool calls
    assert ns.total_tool_calls == 3


def test_turn_duration():
    ns = _get_normalized()
    # Turn 1 has a turn_duration system event with 3000ms
    # But turn_duration is keyed by parentUuid of the system event, which is a2
    # The last event in turn 1 is a2 (uuid)
    # So duration should be found
    # Actually the turn_duration lookup uses parentUuid of the system event
    # s1 has parentUuid=a2, so turn_durations["a2"] = 3000
    # _flush_turn checks if last_uuid (a2) is in turn_durations
    assert ns.turns[0].duration_ms == 3000
