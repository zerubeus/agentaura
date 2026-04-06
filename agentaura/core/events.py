"""Raw Pydantic models matching the exact Claude Code JSONL session schema.

These models preserve all source IDs and fields as they appear in the JSONL files.
No normalization or interpretation is done at this layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, Field

# --- Content blocks inside messages ---


class ThinkingBlock(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    signature: str | None = None


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str = ""


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    source: dict[str, Any] = Field(default_factory=dict)


class DocumentBlock(BaseModel):
    type: Literal["document"] = "document"
    source: dict[str, Any] = Field(default_factory=dict)


class ToolResultContent(BaseModel):
    """Content inside a tool_result block (text or image)."""

    type: str  # "text", "image", etc.
    text: str | None = None
    source: dict[str, Any] | None = None


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | list[ToolResultContent] = ""
    is_error: bool | None = None


ContentBlock = Annotated[
    ThinkingBlock | TextBlock | ToolUseBlock | ToolResultBlock | ImageBlock | DocumentBlock,
    Field(discriminator="type"),
]


# --- Token usage ---


class CacheCreationDetail(BaseModel):
    ephemeral_1h_input_tokens: int = 0
    ephemeral_5m_input_tokens: int = 0


class ServerToolUse(BaseModel):
    web_search_requests: int = 0
    web_fetch_requests: int = 0


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation: CacheCreationDetail | None = None
    server_tool_use: ServerToolUse | None = None
    service_tier: str | None = None
    speed: str | None = None
    inference_geo: str | None = None
    iterations: list[Any] | None = None


# --- Messages ---


class AssistantMessage(BaseModel):
    id: str | None = None
    role: Literal["assistant"] = "assistant"
    type: str | None = None
    model: str | None = None
    content: list[ContentBlock] = Field(default_factory=list)
    stop_reason: str | None = None
    stop_details: dict[str, Any] | None = None
    stop_sequence: str | None = None
    usage: TokenUsage | None = None


class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: str | list[ContentBlock] = ""


# --- Subagent tool result (embedded in user events) ---


class SubagentToolResult(BaseModel):
    status: str | None = None
    prompt: str | None = None
    agent_id: str | None = Field(None, alias="agentId")
    content: Any = None
    total_duration_ms: int | None = Field(None, alias="totalDurationMs")
    total_tokens: int | None = Field(None, alias="totalTokens")
    total_tool_use_count: int | None = Field(None, alias="totalToolUseCount")
    usage: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


# toolUseResult can be a dict (SubagentToolResult), a string (error message), or a list
ToolUseResultField = SubagentToolResult | str | list[Any] | None


# --- Attachment types ---


class DeferredToolsDelta(BaseModel):
    type: Literal["deferred_tools_delta"] = "deferred_tools_delta"
    added_names: list[str] = Field(default_factory=list, alias="addedNames")
    removed_names: list[str] = Field(default_factory=list, alias="removedNames")

    model_config = {"populate_by_name": True}


class McpInstructionsDelta(BaseModel):
    type: Literal["mcp_instructions_delta"] = "mcp_instructions_delta"
    added_names: list[str] = Field(default_factory=list, alias="addedNames")
    removed_names: list[str] = Field(default_factory=list, alias="removedNames")

    model_config = {"populate_by_name": True}


AttachmentData = DeferredToolsDelta | McpInstructionsDelta


# --- Hook info ---


class HookInfo(BaseModel):
    command: str | None = None
    duration_ms: int | None = Field(None, alias="durationMs")

    model_config = {"populate_by_name": True}


# --- Origin ---


class MessageOrigin(BaseModel):
    kind: str | None = None


# --- MCP Meta ---


class McpMeta(BaseModel):
    structured_content: dict[str, Any] | None = Field(None, alias="structuredContent")

    model_config = {"populate_by_name": True}


# --- File history snapshot ---


class FileHistorySnapshot(BaseModel):
    message_id: str | None = Field(None, alias="messageId")
    tracked_file_backups: dict[str, Any] = Field(default_factory=dict, alias="trackedFileBackups")
    timestamp: str | None = None

    model_config = {"populate_by_name": True}


# --- Hook progress data ---


class HookProgressData(BaseModel):
    type: Literal["hook_progress"] = "hook_progress"
    command: str | None = None
    hook_event: str | None = Field(None, alias="hookEvent")
    hook_name: str | None = Field(None, alias="hookName")

    model_config = {"populate_by_name": True}


# --- Base fields shared across conversational events ---


class ConversationEventBase(BaseModel):
    """Fields common to user, assistant, system, progress, attachment events."""

    uuid: str | None = None
    parent_uuid: str | None = Field(None, alias="parentUuid")
    timestamp: datetime | None = None
    session_id: str | None = Field(None, alias="sessionId")
    is_sidechain: bool | None = Field(None, alias="isSidechain")
    cwd: str | None = None
    git_branch: str | None = Field(None, alias="gitBranch")
    version: str | None = None
    slug: str | None = None
    entrypoint: str | None = None
    user_type: str | None = Field(None, alias="userType")

    model_config = {"populate_by_name": True}


# --- Event types ---


class UserEvent(ConversationEventBase):
    type: Literal["user"] = "user"
    message: UserMessage | None = None
    prompt_id: str | None = Field(None, alias="promptId")
    permission_mode: str | None = Field(None, alias="permissionMode")
    is_meta: bool | None = Field(None, alias="isMeta")
    tool_use_result: ToolUseResultField = Field(None, alias="toolUseResult")
    mcp_meta: McpMeta | None = Field(None, alias="mcpMeta")
    plan_content: str | None = Field(None, alias="planContent")
    origin: MessageOrigin | None = None
    image_paste_ids: list[str | int] | None = Field(None, alias="imagePasteIds")
    source_tool_assistant_uuid: str | None = Field(None, alias="sourceToolAssistantUUID")


class AssistantEvent(ConversationEventBase):
    type: Literal["assistant"] = "assistant"
    message: AssistantMessage | None = None
    request_id: str | None = Field(None, alias="requestId")
    is_api_error_message: bool | None = Field(None, alias="isApiErrorMessage")


class SystemEvent(ConversationEventBase):
    type: Literal["system"] = "system"
    subtype: str | None = None
    content: str | None = None
    # turn_duration fields
    duration_ms: int | None = Field(None, alias="durationMs")
    message_count: int | None = Field(None, alias="messageCount")
    # stop_hook_summary fields
    hook_count: int | None = Field(None, alias="hookCount")
    hook_infos: list[HookInfo] | None = Field(None, alias="hookInfos")
    hook_errors: list[Any] | None = Field(None, alias="hookErrors")
    stop_reason: str | None = Field(None, alias="stopReason")
    has_output: bool | None = Field(None, alias="hasOutput")
    prevented_continuation: bool | None = Field(None, alias="preventedContinuation")
    level: str | None = None
    is_meta: bool | None = Field(None, alias="isMeta")
    tool_use_id: str | None = Field(None, alias="toolUseID")


class ProgressEvent(ConversationEventBase):
    type: Literal["progress"] = "progress"
    data: HookProgressData | dict[str, Any] | None = None
    tool_use_id: str | None = Field(None, alias="toolUseID")
    parent_tool_use_id: str | None = Field(None, alias="parentToolUseID")


class AttachmentEvent(ConversationEventBase):
    type: Literal["attachment"] = "attachment"
    attachment: AttachmentData | dict[str, Any] | None = None


class QueueOperationEvent(BaseModel):
    type: Literal["queue-operation"] = "queue-operation"
    operation: str  # "enqueue" or "dequeue"
    session_id: str | None = Field(None, alias="sessionId")
    timestamp: datetime | None = None
    content: dict[str, Any] | str | None = None

    model_config = {"populate_by_name": True}


class FileHistorySnapshotEvent(BaseModel):
    type: Literal["file-history-snapshot"] = "file-history-snapshot"
    message_id: str | None = Field(None, alias="messageId")
    snapshot: FileHistorySnapshot | None = None
    is_snapshot_update: bool | None = Field(None, alias="isSnapshotUpdate")

    model_config = {"populate_by_name": True}


class PermissionModeEvent(BaseModel):
    type: Literal["permission-mode"] = "permission-mode"
    permission_mode: str = Field(alias="permissionMode")
    session_id: str | None = Field(None, alias="sessionId")

    model_config = {"populate_by_name": True}


class AgentNameEvent(BaseModel):
    type: Literal["agent-name"] = "agent-name"
    agent_name: str = Field(alias="agentName")
    session_id: str | None = Field(None, alias="sessionId")

    model_config = {"populate_by_name": True}


class CustomTitleEvent(BaseModel):
    type: Literal["custom-title"] = "custom-title"
    custom_title: str = Field(alias="customTitle")
    session_id: str | None = Field(None, alias="sessionId")

    model_config = {"populate_by_name": True}


class LastPromptEvent(BaseModel):
    type: Literal["last-prompt"] = "last-prompt"
    last_prompt: str | None = Field(None, alias="lastPrompt")
    session_id: str | None = Field(None, alias="sessionId")

    model_config = {"populate_by_name": True}


# --- Union type for parsing ---

SessionEvent = (
    UserEvent
    | AssistantEvent
    | SystemEvent
    | ProgressEvent
    | AttachmentEvent
    | QueueOperationEvent
    | FileHistorySnapshotEvent
    | PermissionModeEvent
    | AgentNameEvent
    | CustomTitleEvent
    | LastPromptEvent
)

# Map from type string to event class for parsing
EVENT_TYPE_MAP: dict[str, type[BaseModel]] = {
    "user": UserEvent,
    "assistant": AssistantEvent,
    "system": SystemEvent,
    "progress": ProgressEvent,
    "attachment": AttachmentEvent,
    "queue-operation": QueueOperationEvent,
    "file-history-snapshot": FileHistorySnapshotEvent,
    "permission-mode": PermissionModeEvent,
    "agent-name": AgentNameEvent,
    "custom-title": CustomTitleEvent,
    "last-prompt": LastPromptEvent,
}


def parse_event(raw: dict[str, Any]) -> SessionEvent | None:
    """Parse a raw JSON dict into a typed SessionEvent.

    Returns None for unknown event types rather than raising.
    Uses lenient validation to handle schema variations across Claude Code versions.
    """
    event_type = raw.get("type")
    if event_type not in EVENT_TYPE_MAP:
        return None
    model_cls = EVENT_TYPE_MAP[event_type]
    return cast(SessionEvent, model_cls.model_validate(raw))


# --- Subagent metadata ---


class SubagentMeta(BaseModel):
    """Metadata from {sessionId}/subagents/agent-{id}.meta.json"""

    agent_type: str = Field(alias="agentType")
    description: str | None = None

    model_config = {"populate_by_name": True}
