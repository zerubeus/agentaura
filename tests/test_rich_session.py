"""Comprehensive tests using the rich Claude Code fixture.

Covers: subagents, MCP deltas, file changes, parallel tool calls,
multimodal prompts, API errors, thinking blocks, and all metadata.
"""

from pathlib import Path

from agentaura.adapters.claude_code.parser import parse_session
from agentaura.core.normalized import normalize_session

FIXTURES = Path(__file__).parent / "fixtures"
RICH_SESSION = FIXTURES / "rich_session.jsonl"


def _get_rich():
    parsed = parse_session(RICH_SESSION, project_path="test-project")
    return normalize_session(parsed)


# --- Session-level ---


def test_session_metadata():
    ns = _get_rich()
    assert ns.session_id == "rich_session"
    assert ns.cwd == "/home/user/myapp"
    assert ns.git_branch == "fix/auth-bug"
    assert ns.model == "claude-opus-4-6"
    assert ns.version == "2.1.92"
    assert ns.entrypoint == "cli"
    assert ns.slug == "test-rich-session"


def test_three_turns():
    ns = _get_rich()
    assert len(ns.turns) == 3


# --- Turn 1: Read + parallel Edit+Bash ---


def test_turn1_prompt():
    ns = _get_rich()
    assert "Fix the authentication bug" in ns.turns[0].user_prompt


def test_turn1_generations():
    ns = _get_rich()
    t1 = ns.turns[0]
    # 3 generations: thinking+Read, parallel Edit+Bash, end_turn
    assert len(t1.generations) == 3


def test_turn1_thinking_detected():
    ns = _get_rich()
    assert ns.turns[0].generations[0].has_thinking is True
    assert ns.turns[0].generations[1].has_thinking is False


def test_turn1_read_tool():
    ns = _get_rich()
    gen1 = ns.turns[0].generations[0]
    assert len(gen1.tool_calls) == 1
    assert gen1.tool_calls[0].name == "Read"
    assert "login.py" in gen1.tool_calls[0].input_params.get("file_path", "")
    assert "BUG: no password hashing" in gen1.tool_calls[0].output_content


def test_turn1_parallel_tools():
    ns = _get_rich()
    gen2 = ns.turns[0].generations[1]
    assert len(gen2.tool_calls) == 2
    names = {tc.name for tc in gen2.tool_calls}
    assert names == {"Edit", "Bash"}

    bash_tc = next(tc for tc in gen2.tool_calls if tc.name == "Bash")
    assert "3 passed" in bash_tc.output_content


def test_turn1_duration():
    ns = _get_rich()
    assert ns.turns[0].duration_ms == 10000


def test_turn1_end_turn_response():
    ns = _get_rich()
    gen3 = ns.turns[0].generations[2]
    assert gen3.stop_reason == "end_turn"
    assert "Fixed the authentication bug" in gen3.text_content


# --- Turn 2: Agent subagent ---


def test_turn2_prompt():
    ns = _get_rich()
    assert "review the whole auth module" in ns.turns[1].user_prompt


def test_turn2_agent_tool():
    ns = _get_rich()
    gen = ns.turns[1].generations[0]
    assert len(gen.tool_calls) == 1
    assert gen.tool_calls[0].name == "Agent"


def test_turn2_final_response():
    ns = _get_rich()
    # The turn has 2 generations: Agent call + final summary
    # API error message should be excluded
    gens = ns.turns[1].generations
    assert any("Missing rate limiting" in g.text_content for g in gens)


def test_api_error_excluded():
    ns = _get_rich()
    all_models = [g.model for t in ns.turns for g in t.generations]
    assert "<synthetic>" not in all_models


# --- Turn 3: Multimodal prompt ---


def test_turn3_multimodal_prompt():
    ns = _get_rich()
    t3 = ns.turns[2]
    assert "screenshot of the error" in t3.user_prompt


def test_turn3_response():
    ns = _get_rich()
    gen = ns.turns[2].generations[0]
    assert "session token format" in gen.text_content


# --- Subagents ---


def test_subagent_parsed():
    ns = _get_rich()
    assert len(ns.subagents) == 1
    sa = ns.subagents[0]
    assert sa.agent_type == "Explore"
    assert sa.description == "Review auth module"


def test_subagent_prompt_and_status():
    ns = _get_rich()
    sa = ns.subagents[0]
    assert sa.prompt == "Review the authentication module for security issues"
    assert sa.status == "completed"


def test_subagent_generations():
    ns = _get_rich()
    sa = ns.subagents[0]
    assert len(sa.generations) == 2
    assert sa.generations[0].model == "claude-opus-4-6"


def test_subagent_tool_calls():
    ns = _get_rich()
    sa = ns.subagents[0]
    assert len(sa.tool_calls) == 1
    assert sa.tool_calls[0].name == "Glob"


# --- MCP deltas ---


def test_mcp_deltas():
    ns = _get_rich()
    assert len(ns.mcp_deltas) == 2

    # First: deferred_tools_delta
    d1 = ns.mcp_deltas[0]
    assert "mcp__supabase__execute_sql" in d1.added_tools
    assert "mcp__supabase__list_tables" in d1.added_tools

    # Second: mcp_instructions_delta
    d2 = ns.mcp_deltas[1]
    assert "supabase-auth-guide" in d2.added_instructions


# --- File changes ---


def test_file_changes():
    ns = _get_rich()
    assert len(ns.file_changes) >= 1
    # At least one snapshot has tracked files
    fc = next(fc for fc in ns.file_changes if fc.tracked_files)
    assert "src/main.py" in fc.tracked_files or "src/login.py" in fc.tracked_files


# --- Token totals ---


def test_token_totals():
    ns = _get_rich()
    assert ns.total_input_tokens > 0
    assert ns.total_output_tokens > 0
    assert ns.total_cache_creation_tokens > 0
    assert ns.total_cache_read_tokens > 0


def test_cost_computed():
    ns = _get_rich()
    assert ns.total_cost_usd > 0
    for turn in ns.turns:
        for gen in turn.generations:
            assert gen.cost_usd >= 0


def test_generation_counts():
    ns = _get_rich()
    # 3 turns (6 main gens) + 2 subagent gens = 8
    assert ns.total_generations >= 6
    # Read + Edit + Bash + Agent (main) + Glob (subagent) = 5
    assert ns.total_tool_calls >= 4  # At least main session tools
