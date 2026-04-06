"""Normalize Codex sessions into the agent-agnostic model.

Maps Codex events to the same NormalizedSession/Turn/Generation/ToolCall
hierarchy used by Claude Code, so they render identically in Langfuse.
"""

from __future__ import annotations

from datetime import datetime

from agentaura.adapters.codex.parser import ParsedCodexSession
from agentaura.core.normalized import (
    Generation,
    NormalizedSession,
    ToolCall,
    Turn,
)


def normalize_codex_session(parsed: ParsedCodexSession) -> NormalizedSession:
    """Transform a ParsedCodexSession into a NormalizedSession."""
    meta = parsed.meta
    events = parsed.events

    # Extract metadata from session_meta
    cwd = meta.get("cwd")
    git_info = meta.get("git", {})
    git_branch = git_info.get("branch")
    version = meta.get("cli_version")
    entrypoint = meta.get("source", "cli")

    # Track model from turn_context events
    model: str | None = None
    current_model: str | None = None
    current_effort: str | None = None

    # Build turns from event sequence
    turns: list[Turn] = []
    current_prompt = ""
    current_turn_start: datetime | None = None
    current_generations: list[Generation] = []
    current_tool_calls: list[ToolCall] = []
    turn_number = 0

    # Token tracking from event_msg/token_count
    last_total_input = 0
    last_total_output = 0
    total_input_tokens = 0
    total_output_tokens = 0

    # Pending function call to pair with output
    pending_calls: dict[str, tuple[str, dict, datetime | None]] = {}
    pending_thinking = False

    def _flush_turn() -> None:
        nonlocal current_prompt, current_turn_start, current_generations
        nonlocal current_tool_calls, turn_number
        if not current_generations and not current_tool_calls:
            return

        turn_number += 1
        # Attach tool calls to the last generation if possible
        if current_generations and current_tool_calls:
            current_generations[-1].tool_calls.extend(current_tool_calls)

        turns.append(
            Turn(
                turn_number=turn_number,
                user_prompt=current_prompt[:5000],
                generations=current_generations,
                start_time=current_turn_start,
                end_time=current_generations[-1].start_time if current_generations else None,
                duration_ms=None,
                permission_mode=None,
            )
        )
        current_generations = []
        current_tool_calls = []
        current_prompt = ""
        current_turn_start = None

    for ev in events:
        if ev.type == "turn_context":
            current_model = ev.payload.get("model")
            current_effort = ev.payload.get("effort")
            if current_model and not model:
                model = current_model

        elif ev.type == "event_msg":
            msg_type = ev.payload.get("type", "")

            if msg_type == "user_message":
                _flush_turn()
                current_prompt = ev.payload.get("message", "")
                current_turn_start = ev.timestamp

            elif msg_type == "token_count":
                info = ev.payload.get("info")
                if info:
                    total = info.get("total_token_usage", {})
                    total_input_tokens = total.get("input_tokens", total_input_tokens)
                    total_output_tokens = total.get("output_tokens", total_output_tokens)

                    last_usage = info.get("last_token_usage", {})
                    last_total_input = last_usage.get("input_tokens", 0)
                    last_total_output = last_usage.get("output_tokens", 0)

        elif ev.type == "response_item":
            payload = ev.payload
            item_type = payload.get("type", "")

            if item_type == "message" and payload.get("role") == "user":
                # User prompt via response_item (alternative to event_msg/user_message)
                content_blocks = payload.get("content", [])
                text_parts = [
                    b.get("text", "")
                    for b in content_blocks
                    if b.get("type") in ("input_text", "text")
                ]
                prompt_text = "\n".join(text_parts).strip()
                if prompt_text:
                    _flush_turn()
                    current_prompt = prompt_text
                    current_turn_start = ev.timestamp

            elif item_type == "message" and payload.get("role") == "assistant":
                # Assistant response — create a generation
                content_blocks = payload.get("content", [])
                text_parts = [
                    b.get("text", "")
                    for b in content_blocks
                    if b.get("type") in ("output_text", "text")
                ]
                current_generations.append(
                    Generation(
                        id=payload.get("id", ""),
                        request_id=None,
                        model=current_model or model or "unknown",
                        input_tokens=last_total_input,
                        output_tokens=last_total_output,
                        cache_creation_tokens=0,
                        cache_read_tokens=0,
                        cost_usd=0.0,
                        start_time=ev.timestamp,
                        stop_reason=payload.get("status"),
                        has_thinking=pending_thinking,
                        text_content="\n".join(text_parts),
                        tool_calls=[],
                        service_tier=None,
                        speed=current_effort,
                    )
                )
                pending_thinking = False

            elif item_type == "reasoning":
                # Reasoning block — mark next generation as having thinking
                # Reasoning comes before the assistant message in Codex events
                pending_thinking = True

            elif item_type == "function_call":
                call_id = payload.get("call_id", "")
                pending_calls[call_id] = (
                    payload.get("name", "unknown"),
                    _parse_args(payload.get("arguments", "{}")),
                    ev.timestamp,
                )

            elif item_type == "function_call_output":
                call_id = payload.get("call_id", "")
                output = payload.get("output", "")
                if call_id in pending_calls:
                    name, args, start_time = pending_calls.pop(call_id)
                    current_tool_calls.append(
                        ToolCall(
                            id=call_id,
                            name=name,
                            input_params=args,
                            output_content=str(output)[:2000],
                            output_is_error=False,
                            start_time=start_time,
                            end_time=ev.timestamp,
                        )
                    )

    _flush_turn()

    # Session time range
    start_time = turns[0].start_time if turns else None
    end_time = turns[-1].end_time if turns else None
    all_gens = [g for t in turns for g in t.generations]
    all_tcs = [tc for g in all_gens for tc in g.tool_calls]

    return NormalizedSession(
        session_id=parsed.session_id,
        project_path=cwd or "",
        cwd=cwd,
        git_branch=git_branch,
        model=model,
        version=version,
        entrypoint=entrypoint,
        slug=None,
        start_time=start_time,
        end_time=end_time,
        turns=turns,
        subagents=[],
        total_cost_usd=0.0,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_cache_creation_tokens=0,
        total_cache_read_tokens=0,
        total_generations=len(all_gens),
        total_tool_calls=len(all_tcs),
    )


def _parse_args(args_str: str) -> dict:
    """Parse function call arguments JSON string."""
    try:
        import json

        return json.loads(args_str)
    except (json.JSONDecodeError, TypeError):
        return {"raw": args_str}
