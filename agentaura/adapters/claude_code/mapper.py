"""Map normalized sessions to Langfuse via pure OpenTelemetry SDK.

Bypasses the Langfuse Python SDK wrapper to emit spans directly via
the OTel SDK, which allows setting explicit start_time/end_time on
historical imports. Uses Langfuse-recognized span attributes so the
OTLP ingestion endpoint creates proper observations.

Span hierarchy:
    Root (type=span, as_root) → session
      ├── turn-N (type=span, input=prompt)
      │   ├── llm-call (type=generation, model, input, output, usage, cost)
      │   │   └── tool:X (type=tool, input, output)
      │   └── llm-call ...
      ├── subagent:Type (type=agent, input=prompt, status)
      │   └── llm-call ...
      ├── mcp:tools-changed (type=event)
      └── file-change (type=event)
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace as trace_api
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from agentaura.core.normalized import (
    FileChange,
    Generation,
    McpToolDelta,
    NormalizedSession,
    SubagentSpawn,
    ToolCall,
    Turn,
)

# --- Provider management ---

_providers: dict[str, TracerProvider] = {}


def _get_provider(endpoint: str) -> TracerProvider:
    if endpoint not in _providers:
        resource = Resource.create({"service.name": "agentaura"})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        _providers[endpoint] = provider
    return _providers[endpoint]


def flush(endpoint: str = "http://localhost:4318") -> bool:
    """Force flush all pending spans to the collector.

    Returns True if flush succeeded, False if it failed or timed out.
    """
    if endpoint not in _providers:
        return True
    return _providers[endpoint].force_flush(timeout_millis=10_000)


# --- ID generation ---


def _stable_trace_id(session_id: str) -> int:
    """Deterministic 128-bit trace ID from session_id."""
    return int(hashlib.md5(session_id.encode()).hexdigest(), 16)


def _stable_span_id(session_id: str, suffix: str) -> int:
    """Deterministic 64-bit span ID from session_id + suffix."""
    raw = hashlib.md5(f"{session_id}:{suffix}".encode()).hexdigest()
    return int(raw[:16], 16)


# --- Timestamp helpers ---


def _ns(dt: datetime | None) -> int | None:
    """Convert datetime to nanoseconds since epoch."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1_000_000_000)


def _ns_or_now(dt: datetime | None) -> int:
    """Convert datetime to ns, falling back to now."""
    result = _ns(dt)
    if result is not None:
        return result
    return int(datetime.now(UTC).timestamp() * 1_000_000_000)


# --- Serialization ---


def _ser(obj: Any) -> str | None:
    """JSON-serialize for Langfuse attributes. Returns None for None."""
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, default=str)


# --- Span builders ---


def _export_tool_call(
    tracer: trace_api.Tracer,
    parent_ctx: Context,
    tc: ToolCall,
) -> None:
    start_ns = _ns_or_now(tc.start_time)
    end_ns = _ns(tc.end_time) or start_ns + 1_000_000

    span = tracer.start_span(
        name=f"tool:{tc.name}",
        context=parent_ctx,
        start_time=start_ns,
        attributes={
            "langfuse.observation.type": "tool",
            "langfuse.observation.input": _ser(tc.input_params) or "",
            "langfuse.observation.output": tc.output_content[:2000] if tc.output_content else "",
            "langfuse.observation.metadata.tool_use_id": tc.id,
            "langfuse.observation.metadata.is_error": str(tc.output_is_error),
        },
    )
    span.end(end_time=end_ns)


def _export_generation(
    tracer: trace_api.Tracer,
    parent_ctx: Context,
    gen: Generation,
    user_prompt: str,
    next_start: datetime | None = None,
) -> None:
    start_ns = _ns_or_now(gen.start_time)

    usage: dict[str, int] = {}
    if gen.input_tokens:
        usage["input"] = gen.input_tokens
    if gen.output_tokens:
        usage["output"] = gen.output_tokens
    if gen.cache_creation_tokens:
        usage["cache_creation_input_tokens"] = gen.cache_creation_tokens
    if gen.cache_read_tokens:
        usage["cache_read_input_tokens"] = gen.cache_read_tokens

    attrs: dict[str, str | int | float | bool] = {
        "langfuse.observation.type": "generation",
        "langfuse.observation.model.name": gen.model,
        "langfuse.observation.input": user_prompt[:5000],
        "langfuse.observation.output": gen.text_content[:5000] if gen.text_content else "",
        "langfuse.observation.metadata.request_id": gen.request_id or "",
        "langfuse.observation.metadata.stop_reason": gen.stop_reason or "",
        "langfuse.observation.metadata.has_thinking": str(gen.has_thinking),
        "langfuse.observation.metadata.service_tier": gen.service_tier or "",
        "langfuse.observation.metadata.speed": gen.speed or "",
    }
    if usage:
        attrs["langfuse.observation.usage_details"] = json.dumps(usage)
    if gen.cost_usd > 0:
        attrs["langfuse.observation.cost_details"] = json.dumps({"total": gen.cost_usd})

    span = tracer.start_span(
        name="llm-call",
        context=parent_ctx,
        start_time=start_ns,
        attributes=attrs,
    )
    gen_ctx = trace_api.set_span_in_context(span)

    for tc in gen.tool_calls:
        _export_tool_call(tracer, gen_ctx, tc)

    # End time: use next generation's start, or last tool end, or start+1ms
    end_candidates = [start_ns + 1_000_000]
    if next_start:
        ns = _ns(next_start)
        if ns and ns > start_ns:
            end_candidates.append(ns)
    for tc in gen.tool_calls:
        tc_end = _ns(tc.end_time)
        if tc_end and tc_end > start_ns:
            end_candidates.append(tc_end + 1_000)
    span.end(end_time=max(end_candidates))


def _export_turn(
    tracer: trace_api.Tracer,
    parent_ctx: Context,
    turn: Turn,
) -> None:
    start_ns = _ns_or_now(turn.start_time)

    span = tracer.start_span(
        name=f"turn-{turn.turn_number}",
        context=parent_ctx,
        start_time=start_ns,
        attributes={
            "langfuse.observation.type": "span",
            "langfuse.observation.input": turn.user_prompt[:5000] if turn.user_prompt else "",
            "langfuse.observation.metadata.permission_mode": turn.permission_mode or "",
            "langfuse.observation.metadata.duration_ms": str(turn.duration_ms or ""),
            "langfuse.observation.metadata.generation_count": str(len(turn.generations)),
        },
    )
    turn_ctx = trace_api.set_span_in_context(span)

    gens = turn.generations
    for idx, gen in enumerate(gens):
        if idx + 1 < len(gens):
            next_start = gens[idx + 1].start_time
        else:
            # Last generation: don't use turn.end_time (it equals gen.start_time)
            # Use turn duration if available to infer actual end
            next_start = None
            if turn.duration_ms and turn.start_time:
                from datetime import timedelta

                next_start = turn.start_time + timedelta(milliseconds=turn.duration_ms)
        _export_generation(tracer, turn_ctx, gen, turn.user_prompt, next_start=next_start)

    # Turn must end after all children
    end_candidates = [_ns(turn.end_time) or start_ns + 1_000_000]
    for gen in gens:
        gs = _ns(gen.start_time)
        if gs:
            end_candidates.append(gs + 2_000_000)  # generation +1ms + buffer
        for tc in gen.tool_calls:
            te = _ns(tc.end_time)
            if te:
                end_candidates.append(te + 1_000_000)
    span.end(end_time=max(end_candidates))


def _export_subagent(
    tracer: trace_api.Tracer,
    parent_ctx: Context,
    sa: SubagentSpawn,
) -> None:
    start_ns = _ns_or_now(sa.generations[0].start_time if sa.generations else None)
    # End after all children: last generation start + its tool calls
    end_candidates = [start_ns + 1_000_000]
    for gen in sa.generations:
        gs = _ns(gen.start_time)
        if gs:
            end_candidates.append(gs + 1_000_000)
        for tc in gen.tool_calls:
            te = _ns(tc.end_time)
            if te:
                end_candidates.append(te + 1_000)
    end_ns = max(end_candidates)

    span = tracer.start_span(
        name=f"subagent:{sa.agent_type or sa.agent_id}",
        context=parent_ctx,
        start_time=start_ns,
        attributes={
            "langfuse.observation.type": "agent",
            "langfuse.observation.input": sa.prompt or sa.description or "",
            "langfuse.observation.output": sa.description or "",
            "langfuse.observation.metadata.agent_id": sa.agent_id,
            "langfuse.observation.metadata.agent_type": sa.agent_type or "",
            "langfuse.observation.metadata.status": sa.status or "",
            "langfuse.observation.metadata.event_count": str(sa.event_count),
            "langfuse.observation.metadata.total_cost_usd": str(sa.total_cost_usd),
            "langfuse.observation.metadata.total_input_tokens": str(sa.total_input_tokens),
            "langfuse.observation.metadata.total_output_tokens": str(sa.total_output_tokens),
        },
    )
    sa_ctx = trace_api.set_span_in_context(span)

    sa_gens = sa.generations
    for idx, gen in enumerate(sa_gens):
        next_start = sa_gens[idx + 1].start_time if idx + 1 < len(sa_gens) else None
        _export_generation(
            tracer, sa_ctx, gen, sa.prompt or sa.description or "", next_start=next_start
        )

    span.end(end_time=end_ns)


def _export_mcp_deltas(
    tracer: trace_api.Tracer,
    parent_ctx: Context,
    deltas: list[McpToolDelta],
) -> None:
    for delta in deltas:
        start_ns = _ns_or_now(delta.timestamp)
        attrs: dict[str, str] = {
            "langfuse.observation.type": "event",
        }
        if delta.added_tools or delta.removed_tools:
            attrs["langfuse.observation.metadata.added_tools"] = json.dumps(delta.added_tools)
            attrs["langfuse.observation.metadata.removed_tools"] = json.dumps(delta.removed_tools)
        if delta.added_instructions or delta.removed_instructions:
            attrs["langfuse.observation.metadata.added_instructions"] = json.dumps(
                delta.added_instructions
            )
            attrs["langfuse.observation.metadata.removed_instructions"] = json.dumps(
                delta.removed_instructions
            )

        if delta.added_tools or delta.removed_tools:
            name = "mcp:tools-changed"
        else:
            name = "mcp:instructions-changed"
        span = tracer.start_span(
            name=name, context=parent_ctx, start_time=start_ns, attributes=attrs
        )
        span.end(end_time=start_ns + 1_000)


def _export_file_changes(
    tracer: trace_api.Tracer,
    parent_ctx: Context,
    changes: list[FileChange],
) -> None:
    for change in changes:
        # Parse string timestamp from snapshot
        change_dt: datetime | None = None
        if change.timestamp:
            try:
                change_dt = datetime.fromisoformat(str(change.timestamp).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        start_ns = _ns_or_now(change_dt)
        files = list(change.tracked_files.keys())
        span = tracer.start_span(
            name="file-change",
            context=parent_ctx,
            start_time=start_ns,
            attributes={
                "langfuse.observation.type": "event",
                "langfuse.observation.metadata.files": json.dumps(files[:50]),
                "langfuse.observation.metadata.file_count": str(len(files)),
            },
        )
        span.end(end_time=start_ns + 1_000)


# --- Main export function ---


def export_session(
    session: NormalizedSession,
    endpoint: str = "http://localhost:4318",
) -> str:
    """Export a normalized session to Langfuse via pure OTel.

    Uses deterministic trace ID from session_id for idempotent re-exports.
    Sets explicit start_time/end_time on all spans for historical accuracy.
    """
    provider = _get_provider(endpoint)
    tracer = provider.get_tracer("agentaura", "0.1.0")

    # Create deterministic parent context so all child spans share the same trace ID
    trace_id = _stable_trace_id(session.session_id)
    root_span_id = _stable_span_id(session.session_id, "root")
    parent_ctx = trace_api.set_span_in_context(
        NonRecordingSpan(
            SpanContext(
                trace_id=trace_id,
                span_id=root_span_id,
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
        )
    )

    start_ns = _ns_or_now(session.start_time)

    tags = [
        f"model:{session.model}" if session.model else "model:unknown",
        f"project:{session.project_path}",
    ]

    root_span = tracer.start_span(
        name=session.slug or f"session-{session.session_id[:8]}",
        context=parent_ctx,
        start_time=start_ns,
        attributes={
            "langfuse.internal.as_root": True,
            "session.id": session.session_id,
            "langfuse.trace.name": session.slug or f"session-{session.session_id[:8]}",
            "langfuse.trace.tags": tags,
            "langfuse.trace.metadata.project_path": session.project_path,
            "langfuse.trace.metadata.cwd": session.cwd or "",
            "langfuse.trace.metadata.git_branch": session.git_branch or "",
            "langfuse.trace.metadata.model": session.model or "",
            "langfuse.trace.metadata.entrypoint": session.entrypoint or "",
            "langfuse.version": session.version or "",
            "langfuse.observation.type": "span",
            "langfuse.observation.metadata.session_id": session.session_id,
            "langfuse.observation.metadata.total_cost_usd": str(session.total_cost_usd),
            "langfuse.observation.metadata.total_input_tokens": str(session.total_input_tokens),
            "langfuse.observation.metadata.total_output_tokens": str(session.total_output_tokens),
            "langfuse.observation.metadata.total_generations": str(session.total_generations),
            "langfuse.observation.metadata.total_tool_calls": str(session.total_tool_calls),
            "langfuse.observation.metadata.turn_count": str(len(session.turns)),
            "langfuse.observation.metadata.subagent_count": str(len(session.subagents)),
        },
    )
    root_ctx = trace_api.set_span_in_context(root_span)

    for turn in session.turns:
        _export_turn(tracer, root_ctx, turn)

    for sa in session.subagents:
        _export_subagent(tracer, root_ctx, sa)

    if session.mcp_deltas:
        _export_mcp_deltas(tracer, root_ctx, session.mcp_deltas)

    if session.file_changes:
        _export_file_changes(tracer, root_ctx, session.file_changes)

    # Root must end after all children (turns, subagents, mcp events, file changes)
    end_candidates = [_ns(session.end_time) or start_ns + 1_000_000]
    for turn in session.turns:
        ts = _ns(turn.start_time)
        if ts:
            end_candidates.append(ts + 3_000_000)
        te = _ns(turn.end_time)
        if te:
            end_candidates.append(te + 3_000_000)
        for gen in turn.generations:
            gs = _ns(gen.start_time)
            if gs:
                end_candidates.append(gs + 3_000_000)
            for tc in gen.tool_calls:
                tce = _ns(tc.end_time)
                if tce:
                    end_candidates.append(tce + 2_000_000)
    for sa in session.subagents:
        for gen in sa.generations:
            gs = _ns(gen.start_time)
            if gs:
                end_candidates.append(gs + 3_000_000)
            for tc in gen.tool_calls:
                tce = _ns(tc.end_time)
                if tce:
                    end_candidates.append(tce + 2_000_000)
    for delta in session.mcp_deltas:
        dt = _ns(delta.timestamp)
        if dt:
            end_candidates.append(dt + 1_000_000)
    root_span.end(end_time=max(end_candidates))

    return session.session_id
