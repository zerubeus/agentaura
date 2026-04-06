"""Normalized session models built on top of raw events.

Transforms the flat JSONL event stream into a hierarchical structure:
    Session → Turn[] → (Generation[], ToolCall[], SubagentSpawn[])

This is the agent-agnostic intermediate representation that mappers
convert into backend-specific objects (e.g., Langfuse traces).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentaura.adapters.claude_code.parser import ParsedSession


@dataclass
class ToolCall:
    """A single tool invocation within a turn."""

    id: str  # tool_use block id (e.g., toolu_01...)
    name: str  # tool name (e.g., Read, Bash, Glob)
    input_params: dict[str, object]
    output_content: str  # tool result (truncated for large results)
    output_is_error: bool
    start_time: datetime | None
    end_time: datetime | None


@dataclass
class Generation:
    """A single LLM API call (one assistant event with usage)."""

    id: str
    request_id: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: float
    start_time: datetime | None
    stop_reason: str | None
    has_thinking: bool
    text_content: str
    tool_calls: list[ToolCall]
    service_tier: str | None
    speed: str | None


@dataclass
class SubagentSpawn:
    """A subagent spawned during a session."""

    agent_id: str
    agent_type: str | None
    description: str | None
    event_count: int
    generations: list[Generation] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0


@dataclass
class Turn:
    """A user-initiated turn: user prompt -> model response(s) -> end."""

    turn_number: int
    user_prompt: str
    generations: list[Generation]
    start_time: datetime | None
    end_time: datetime | None
    duration_ms: int | None
    permission_mode: str | None


@dataclass
class NormalizedSession:
    """A fully normalized coding session."""

    session_id: str
    project_path: str
    cwd: str | None
    git_branch: str | None
    model: str | None
    version: str | None
    entrypoint: str | None
    slug: str | None
    start_time: datetime | None
    end_time: datetime | None
    turns: list[Turn]
    subagents: list[SubagentSpawn]
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_cache_creation_tokens: int
    total_cache_read_tokens: int
    total_generations: int
    total_tool_calls: int


def _extract_user_prompt(
    content: str | Sequence[object],
) -> str | None:
    """Extract prompt text from user message content.

    Returns the prompt string if this is a user-initiated prompt (new turn).
    Returns None if this is a tool_result response (not a new turn).

    Handles both:
    - String content (simple text prompt)
    - List content with text/image/document blocks (multimodal prompt)
    - List content with only tool_result blocks (not a prompt)
    """
    from agentaura.core.events import TextBlock, ToolResultBlock

    if isinstance(content, str):
        return content if content.strip() else None

    if not isinstance(content, list) or not content:
        return None

    # If the list contains any tool_result blocks, this is a tool response, not a prompt
    has_tool_result = any(isinstance(b, ToolResultBlock) for b in content)
    if has_tool_result:
        return None

    # Extract text from text blocks
    text_parts: list[str] = []
    for block in content:
        if isinstance(block, TextBlock):
            text_parts.append(block.text)

    prompt = "\n".join(text_parts).strip()
    return prompt if prompt else "[multimodal prompt]"


def _extract_tool_results_from_events(
    events: list[object],
    sidecar_results: dict[str, str] | None = None,
) -> dict[str, tuple[str, bool, datetime | None]]:
    """Build tool_use_id -> (result_text, is_error, timestamp) from user events.

    When inline content is empty or truncated, falls back to sidecar files
    from the tool-results/ directory (keyed by hash).
    """
    from agentaura.core.events import ToolResultBlock, ToolResultContent, UserEvent

    # Build a set of all sidecar content for fuzzy fallback
    sidecar_values = set((sidecar_results or {}).values())

    results: dict[str, tuple[str, bool, datetime | None]] = {}
    for ev in events:
        if not isinstance(ev, UserEvent) or ev.message is None:
            continue
        content = ev.message.content
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, ToolResultBlock):
                continue
            result_text = ""
            if isinstance(block.content, str):
                result_text = block.content
            elif isinstance(block.content, list):
                parts: list[str] = []
                for c in block.content:
                    if isinstance(c, ToolResultContent) and c.text:
                        parts.append(c.text)
                result_text = " ".join(parts)

            # If inline result is empty/short and we have sidecars, try to find
            # a sidecar match. Claude Code stores large tool outputs in
            # {sessionId}/tool-results/{hash}.txt to keep JSONL compact.
            if not result_text and sidecar_results:
                # The sidecar hash isn't directly linked to tool_use_id in the JSONL,
                # so we include all sidecar content as a pool. For now, attach the
                # first unused sidecar that hasn't been claimed yet.
                for hash_key, sidecar_text in list((sidecar_results or {}).items()):
                    if sidecar_text in sidecar_values:
                        result_text = sidecar_text
                        sidecar_values.discard(sidecar_text)
                        break

            results[block.tool_use_id] = (
                result_text[:2000],
                bool(block.is_error),
                ev.timestamp,
            )
    return results


def _build_generation_from_assistant(
    ev: object,
    tool_results: dict[str, tuple[str, bool, datetime | None]],
    model_counts: dict[str, int],
) -> Generation | None:
    """Build a Generation from an AssistantEvent. Returns None if not applicable."""
    from agentaura.core.events import (
        AssistantEvent,
        TextBlock,
        ThinkingBlock,
        ToolUseBlock,
    )
    from agentaura.core.pricing import compute_cost

    if not isinstance(ev, AssistantEvent) or ev.message is None:
        return None
    if ev.is_api_error_message:
        return None

    msg = ev.message
    usage = msg.usage
    if usage is None:
        return None

    model = msg.model or "<unknown>"

    if model != "<synthetic>":
        model_counts[model] = model_counts.get(model, 0) + 1

    cost = compute_cost(usage, model) if model != "<synthetic>" else 0.0

    has_thinking = False
    text_parts: list[str] = []
    tool_call_list: list[ToolCall] = []

    for block in msg.content:
        if isinstance(block, ThinkingBlock):
            has_thinking = True
        elif isinstance(block, TextBlock):
            text_parts.append(block.text)
        elif isinstance(block, ToolUseBlock):
            result_text, is_error, result_ts = tool_results.get(block.id, ("", False, None))
            tool_call_list.append(
                ToolCall(
                    id=block.id,
                    name=block.name,
                    input_params=block.input,
                    output_content=result_text,
                    output_is_error=is_error,
                    start_time=ev.timestamp,
                    end_time=result_ts,
                )
            )

    return Generation(
        id=ev.uuid or "",
        request_id=ev.request_id,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_tokens=usage.cache_creation_input_tokens,
        cache_read_tokens=usage.cache_read_input_tokens,
        cost_usd=cost,
        start_time=ev.timestamp,
        stop_reason=msg.stop_reason,
        has_thinking=has_thinking,
        text_content="\n".join(text_parts),
        tool_calls=tool_call_list,
        service_tier=usage.service_tier,
        speed=usage.speed,
    )


def normalize_session(parsed: ParsedSession) -> NormalizedSession:
    """Transform a ParsedSession into a NormalizedSession."""
    from agentaura.core.events import AssistantEvent, SystemEvent, UserEvent

    events = parsed.events

    # --- Extract session-level metadata ---
    cwd: str | None = None
    git_branch: str | None = None
    version: str | None = None
    entrypoint: str | None = None
    slug: str | None = None
    for ev in events:
        if isinstance(ev, (UserEvent, AssistantEvent)) and ev.cwd:
            cwd = cwd or ev.cwd
            git_branch = git_branch or ev.git_branch
            version = version or ev.version
            entrypoint = entrypoint or ev.entrypoint
            slug = slug or ev.slug
            if all([cwd, git_branch, version, entrypoint, slug]):
                break

    # --- Build lookups ---
    tool_results = _extract_tool_results_from_events(events, parsed.tool_results)  # type: ignore[arg-type]

    turn_durations: dict[str | None, int] = {}
    for ev in events:
        if isinstance(ev, SystemEvent) and ev.subtype == "turn_duration" and ev.duration_ms:
            turn_durations[ev.parent_uuid] = ev.duration_ms

    # --- Build turns ---
    turns: list[Turn] = []
    current_turn_assistants: list[AssistantEvent] = []
    current_turn_prompt = ""
    current_turn_start: datetime | None = None
    current_turn_permission: str | None = None
    turn_number = 0
    model_counts: dict[str, int] = {}

    def _flush_turn() -> None:
        nonlocal current_turn_assistants, current_turn_prompt, current_turn_start
        nonlocal current_turn_permission, turn_number
        if not current_turn_assistants:
            return

        turn_number += 1
        generations: list[Generation] = []
        for assistant_ev in current_turn_assistants:
            gen = _build_generation_from_assistant(assistant_ev, tool_results, model_counts)
            if gen is not None:
                generations.append(gen)

        end_time = generations[-1].start_time if generations else None
        last_uuid = current_turn_assistants[-1].uuid
        duration_ms = turn_durations.get(last_uuid)

        turns.append(
            Turn(
                turn_number=turn_number,
                user_prompt=current_turn_prompt[:5000],
                generations=generations,
                start_time=current_turn_start,
                end_time=end_time,
                duration_ms=duration_ms,
                permission_mode=current_turn_permission,
            )
        )

        current_turn_assistants = []
        current_turn_prompt = ""
        current_turn_start = None
        current_turn_permission = None

    for ev in events:
        if isinstance(ev, UserEvent) and ev.message:
            content = ev.message.content
            prompt_text = _extract_user_prompt(content)
            if prompt_text is not None:
                _flush_turn()
                current_turn_prompt = prompt_text
                current_turn_start = ev.timestamp
                current_turn_permission = ev.permission_mode
        elif isinstance(ev, AssistantEvent) and ev.message:
            if ev.is_api_error_message:
                continue
            if ev.message.usage:
                current_turn_assistants.append(ev)

    _flush_turn()

    # --- Parse subagents ---
    subagent_spawns: list[SubagentSpawn] = []
    for sa in parsed.subagents:
        sa_tool_results = _extract_tool_results_from_events(sa.events)  # type: ignore[arg-type]
        sa_generations: list[Generation] = []
        sa_tool_calls: list[ToolCall] = []
        sa_model_counts: dict[str, int] = {}

        for ev in sa.events:
            gen = _build_generation_from_assistant(ev, sa_tool_results, sa_model_counts)
            if gen is not None:
                sa_generations.append(gen)
                sa_tool_calls.extend(gen.tool_calls)

        subagent_spawns.append(
            SubagentSpawn(
                agent_id=sa.agent_id,
                agent_type=sa.meta.agent_type if sa.meta else None,
                description=sa.meta.description if sa.meta else None,
                event_count=len(sa.events),
                generations=sa_generations,
                tool_calls=sa_tool_calls,
                total_cost_usd=sum(g.cost_usd for g in sa_generations),
                total_input_tokens=sum(g.input_tokens for g in sa_generations),
                total_output_tokens=sum(g.output_tokens for g in sa_generations),
            )
        )

    # --- Aggregate totals ---
    all_gens = [g for t in turns for g in t.generations]
    sa_gens = [g for sa in subagent_spawns for g in sa.generations]
    all_tool_calls = [tc for g in all_gens for tc in g.tool_calls]
    sa_tcs = [tc for sa in subagent_spawns for tc in sa.tool_calls]

    primary_model = max(model_counts, key=lambda k: model_counts[k]) if model_counts else None
    start_time = turns[0].start_time if turns else None
    end_time = turns[-1].end_time if turns else None

    return NormalizedSession(
        session_id=parsed.session_id,
        project_path=parsed.project_path,
        cwd=cwd,
        git_branch=git_branch,
        model=primary_model,
        version=version,
        entrypoint=entrypoint,
        slug=slug,
        start_time=start_time,
        end_time=end_time,
        turns=turns,
        subagents=subagent_spawns,
        total_cost_usd=sum(g.cost_usd for g in all_gens) + sum(g.cost_usd for g in sa_gens),
        total_input_tokens=sum(g.input_tokens for g in all_gens)
        + sum(g.input_tokens for g in sa_gens),
        total_output_tokens=sum(g.output_tokens for g in all_gens)
        + sum(g.output_tokens for g in sa_gens),
        total_cache_creation_tokens=sum(g.cache_creation_tokens for g in all_gens)
        + sum(g.cache_creation_tokens for g in sa_gens),
        total_cache_read_tokens=sum(g.cache_read_tokens for g in all_gens)
        + sum(g.cache_read_tokens for g in sa_gens),
        total_generations=len(all_gens) + len(sa_gens),
        total_tool_calls=len(all_tool_calls) + len(sa_tcs),
    )
