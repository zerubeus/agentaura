"""Map normalized Claude Code sessions to Langfuse traces/spans/generations.

Uses the Langfuse Python SDK v4 low-level API to create:
    NormalizedSession → root span (with session_id via propagate_attributes)
        Turn → child span
            Generation → generation (LLM API call with model, usage, cost)
                ToolCall → child span (tool invocation)
        SubagentSpawn → child span (subagent with nested generations/tools)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langfuse import Langfuse
    from langfuse._client.span import (
        LangfuseAgent,
        LangfuseGeneration,
        LangfuseSpan,
    )

    from agentaura.core.normalized import (
        Generation,
        NormalizedSession,
        SubagentSpawn,
        ToolCall,
        Turn,
    )

    # Any observation type that supports start_observation
    ObservationParent = LangfuseSpan | LangfuseGeneration | LangfuseAgent


def _export_tool_call(parent: ObservationParent, tc: ToolCall) -> None:
    """Create a Langfuse span for a tool call under the given parent."""
    tool_span = parent.start_observation(
        name=f"tool:{tc.name}",
        as_type="tool",
        input=tc.input_params,
        output=tc.output_content[:2000] if tc.output_content else None,
        metadata={
            "tool_use_id": tc.id,
            "is_error": tc.output_is_error,
        },
    )
    tool_span.end()


def _export_generation(parent: ObservationParent, gen: Generation) -> None:
    """Create a Langfuse generation for an LLM API call."""
    usage_details: dict[str, int] = {}
    if gen.input_tokens:
        usage_details["input"] = gen.input_tokens
    if gen.output_tokens:
        usage_details["output"] = gen.output_tokens
    if gen.cache_creation_tokens:
        usage_details["cache_creation_input_tokens"] = gen.cache_creation_tokens
    if gen.cache_read_tokens:
        usage_details["cache_read_input_tokens"] = gen.cache_read_tokens

    gen_obs = parent.start_observation(
        name="llm-call",
        as_type="generation",
        model=gen.model,
        output=gen.text_content[:5000] if gen.text_content else None,
        metadata={
            "request_id": gen.request_id,
            "stop_reason": gen.stop_reason,
            "has_thinking": gen.has_thinking,
            "service_tier": gen.service_tier,
            "speed": gen.speed,
        },
        usage_details=usage_details if usage_details else None,
        cost_details={"total": gen.cost_usd} if gen.cost_usd > 0 else None,
    )

    for tc in gen.tool_calls:
        _export_tool_call(gen_obs, tc)

    gen_obs.end()


def _export_turn(parent: ObservationParent, turn: Turn) -> None:
    """Create a Langfuse span for a turn."""
    turn_span = parent.start_observation(
        name=f"turn-{turn.turn_number}",
        as_type="span",
        input=turn.user_prompt[:5000] if turn.user_prompt else None,
        metadata={
            "permission_mode": turn.permission_mode,
            "duration_ms": turn.duration_ms,
            "generation_count": len(turn.generations),
        },
    )

    for gen in turn.generations:
        _export_generation(turn_span, gen)

    turn_span.end()


def _export_subagent(parent: ObservationParent, sa: SubagentSpawn) -> None:
    """Create a Langfuse span for a subagent."""
    sa_span = parent.start_observation(
        name=f"subagent:{sa.agent_type or sa.agent_id}",
        as_type="agent",
        input=sa.description,
        metadata={
            "agent_id": sa.agent_id,
            "agent_type": sa.agent_type,
            "event_count": sa.event_count,
            "total_cost_usd": sa.total_cost_usd,
            "total_input_tokens": sa.total_input_tokens,
            "total_output_tokens": sa.total_output_tokens,
        },
    )

    for gen in sa.generations:
        _export_generation(sa_span, gen)

    sa_span.end()


def export_session(langfuse: Langfuse, session: NormalizedSession) -> str:
    """Export a normalized session to Langfuse. Returns the session ID."""
    from langfuse import propagate_attributes

    with propagate_attributes(
        session_id=session.session_id,
        version=session.version,
        metadata={
            "project_path": session.project_path,
            "cwd": session.cwd or "",
            "git_branch": session.git_branch or "",
            "model": session.model or "",
            "entrypoint": session.entrypoint or "",
        },
        tags=[
            f"model:{session.model}" if session.model else "model:unknown",
            f"project:{session.project_path}",
        ],
    ):
        trace = langfuse.start_observation(
            name=session.slug or f"session-{session.session_id[:8]}",
            as_type="span",
            metadata={
                "session_id": session.session_id,
                "total_cost_usd": session.total_cost_usd,
                "total_input_tokens": session.total_input_tokens,
                "total_output_tokens": session.total_output_tokens,
                "total_generations": session.total_generations,
                "total_tool_calls": session.total_tool_calls,
                "turn_count": len(session.turns),
                "subagent_count": len(session.subagents),
            },
        )

        for turn in session.turns:
            _export_turn(trace, turn)

        for sa in session.subagents:
            _export_subagent(trace, sa)

        trace.end()

    return session.session_id
