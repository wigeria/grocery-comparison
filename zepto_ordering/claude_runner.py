"""Shared helper for running one Claude Code turn that returns structured output."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query


class AgentError(Exception):
    """Raised when a Claude session fails or returns no usable result."""


async def _single_message(content: list[dict[str, Any]] | str) -> AsyncIterator[dict[str, Any]]:
    """Wrap one user message in the streaming input format.

    The SDK needs streaming input for can_use_tool callbacks and in-process tools.
    """
    yield {
        "type": "user",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
    }


async def run_structured(
    content: list[dict[str, Any]] | str, options: ClaudeAgentOptions, failure: str
) -> tuple[dict[str, Any], str]:
    """Run one turn and return (structured output, session id).

    `options.output_format` must be set. `failure` prefixes the error message.
    """
    result: ResultMessage | None = None
    async for message in query(prompt=_single_message(content), options=options):
        if isinstance(message, ResultMessage):
            result = message

    if result is None:
        raise AgentError(f"{failure}: Claude finished without a result.")
    if result.is_error or not isinstance(result.structured_output, dict):
        details = "; ".join(result.errors or []) or result.result or result.subtype
        raise AgentError(f"{failure}: {details}")
    return result.structured_output, result.session_id
