"""Tests for the pure OTel mapper — verifies span hierarchy, attributes, and timestamps."""

from pathlib import Path

from agentaura.adapters.claude_code.mapper import (
    _stable_trace_id,
    export_session,
)
from agentaura.adapters.claude_code.parser import parse_session
from agentaura.core.normalized import normalize_session
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

FIXTURES = Path(__file__).parent / "fixtures"


class _InMemoryExporter(SpanExporter):
    """Collects spans in a list for testing."""

    def __init__(self) -> None:
        self.spans: list = []

    def export(self, spans):  # type: ignore[override]
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


def _export_and_collect(fixture: str = "rich_session.jsonl") -> list:
    """Export a fixture session and collect spans in memory."""
    import agentaura.adapters.claude_code.mapper as mapper

    # Set up in-memory exporter
    exporter = _InMemoryExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Inject test provider
    test_endpoint = "test://memory"
    mapper._providers[test_endpoint] = provider

    try:
        parsed = parse_session(FIXTURES / fixture, project_path="test")
        normalized = normalize_session(parsed)
        export_session(normalized, endpoint=test_endpoint)
        provider.force_flush()
        return list(exporter.spans)
    finally:
        del mapper._providers[test_endpoint]


def _spans_by_name(spans: list) -> dict[str, list]:
    result: dict[str, list] = {}
    for s in spans:
        result.setdefault(s.name, []).append(s)
    return result


def _get_attr(span, key: str):
    return span.attributes.get(key)


# --- Span hierarchy ---


def test_root_span_exists():
    spans = _export_and_collect()
    roots = [s for s in spans if _get_attr(s, "langfuse.internal.as_root")]
    assert len(roots) == 1
    assert _get_attr(roots[0], "session.id") == "rich_session"


def test_turn_spans_created():
    spans = _export_and_collect()
    turn_spans = [s for s in spans if s.name.startswith("turn-")]
    assert len(turn_spans) == 3


def test_generation_spans_created():
    spans = _export_and_collect()
    gen_spans = [s for s in spans if s.name == "llm-call"]
    # Main session gens + subagent gens
    assert len(gen_spans) >= 5


def test_tool_spans_created():
    spans = _export_and_collect()
    tool_spans = [s for s in spans if s.name.startswith("tool:")]
    assert len(tool_spans) >= 4  # Read, Edit, Bash, Glob(subagent)


def test_subagent_span_created():
    spans = _export_and_collect()
    agent_spans = [s for s in spans if _get_attr(s, "langfuse.observation.type") == "agent"]
    assert len(agent_spans) == 1
    sa = agent_spans[0]
    assert "Explore" in sa.name


def test_mcp_event_spans():
    spans = _export_and_collect()
    mcp_spans = [s for s in spans if s.name.startswith("mcp:")]
    assert len(mcp_spans) == 2


def test_file_change_event_spans():
    spans = _export_and_collect()
    fc_spans = [s for s in spans if s.name == "file-change"]
    assert len(fc_spans) >= 1


# --- Langfuse attributes ---


def test_generation_has_model():
    spans = _export_and_collect()
    gen_spans = [s for s in spans if s.name == "llm-call"]
    for gs in gen_spans:
        model = _get_attr(gs, "langfuse.observation.model.name")
        assert model and model != ""


def test_generation_has_input_and_output():
    spans = _export_and_collect()
    gen_spans = [s for s in spans if s.name == "llm-call"]
    # At least some should have non-empty input/output
    inputs = [_get_attr(gs, "langfuse.observation.input") for gs in gen_spans]
    outputs = [_get_attr(gs, "langfuse.observation.output") for gs in gen_spans]
    assert any(i and len(i) > 0 for i in inputs)
    assert any(o and len(o) > 0 for o in outputs)


def test_generation_has_usage():
    spans = _export_and_collect()
    gen_spans = [s for s in spans if s.name == "llm-call"]
    for gs in gen_spans:
        usage = _get_attr(gs, "langfuse.observation.usage_details")
        assert usage is not None  # JSON string


def test_tool_has_input_and_output():
    spans = _export_and_collect()
    tool_spans = [s for s in spans if s.name.startswith("tool:")]
    for ts in tool_spans:
        assert _get_attr(ts, "langfuse.observation.type") == "tool"
        assert _get_attr(ts, "langfuse.observation.input") is not None


def test_turn_has_prompt_as_input():
    spans = _export_and_collect()
    turn_spans = [s for s in spans if s.name.startswith("turn-")]
    for ts in turn_spans:
        inp = _get_attr(ts, "langfuse.observation.input")
        assert inp and len(inp) > 0


# --- Timestamps ---


def test_spans_have_real_timestamps():
    spans = _export_and_collect()
    for s in spans:
        assert s.start_time is not None
        assert s.end_time is not None
        assert s.end_time >= s.start_time


def test_parent_child_nesting():
    """Parent spans should not end before their children."""
    spans = _export_and_collect()
    span_map = {s.context.span_id: s for s in spans}

    for s in spans:
        parent_id = s.parent.span_id if s.parent else None
        if parent_id and parent_id in span_map:
            parent = span_map[parent_id]
            assert parent.end_time >= s.end_time, (
                f"{parent.name} ends at {parent.end_time} before child "
                f"{s.name} ends at {s.end_time}"
            )


# --- Deterministic IDs ---


def test_stable_trace_id():
    id1 = _stable_trace_id("session-abc")
    id2 = _stable_trace_id("session-abc")
    id3 = _stable_trace_id("session-xyz")
    assert id1 == id2
    assert id1 != id3


def test_same_session_same_trace():
    spans1 = _export_and_collect()
    spans2 = _export_and_collect()
    trace_ids_1 = {s.context.trace_id for s in spans1}
    trace_ids_2 = {s.context.trace_id for s in spans2}
    # All spans share one trace ID, and it's the same across exports
    assert len(trace_ids_1) == 1
    assert trace_ids_1 == trace_ids_2
