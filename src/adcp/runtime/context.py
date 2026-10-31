"""Context strategies applied before each model call.

full: every message. summarized: tool results older than the last KEEP_TURNS assistant turns become
one-line summaries. retrieved: only the top-k tools by keyword overlap with the user input and
recent turns (smaller prompt; can drop a needed tool).
"""

from __future__ import annotations

import re

from adcp.spec import ContextStrategy, ToolSpec
from adcp.types import Message

KEEP_TURNS = 2
TOP_K_TOOLS = 6
SUMMARY_CHARS = 80
_WORD = re.compile(r"[a-z]+")
_STOP = {"the", "a", "an", "to", "of", "and", "or", "for", "my", "i", "is", "in", "on", "by", "it"}


def summarize(messages: list[Message], keep_turns: int = KEEP_TURNS) -> list[Message]:
    idx = [i for i, m in enumerate(messages) if m.role == "assistant"]
    if len(idx) <= keep_turns:
        return list(messages)
    cutoff = idx[-keep_turns]
    out = []
    for i, m in enumerate(messages):
        if i < cutoff and m.role == "tool" and len(m.content) > SUMMARY_CHARS:
            line = " ".join(m.content.split())[:SUMMARY_CHARS]
            out.append(m.model_copy(update={"content": f"[summary {m.name or 'tool'}] {line}..."}))
        else:
            out.append(m)
    return out


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def retrieve_tools(
    tools: list[ToolSpec], user_input: str, messages: list[Message], k: int = TOP_K_TOOLS
) -> list[ToolSpec]:
    recent = messages[-2 * KEEP_TURNS :]
    query = _words(user_input)
    for m in recent:
        query |= _words(m.content[:500])
        for tc in m.tool_calls:
            query |= _words(tc.name.replace("_", " "))
    scored = []
    for pos, t in enumerate(tools):
        name_words = _words(t.name.replace("_", " "))
        score = 2 * len(name_words & query) + len(_words(t.description) & query)
        scored.append((score, -pos, t))
    keep = {id(t) for _, _, t in sorted(scored, key=lambda x: (x[0], x[1]), reverse=True)[:k]}
    return [t for t in tools if id(t) in keep]  # original order keeps the prefix stable


def apply_context(
    strategy: ContextStrategy, messages: list[Message], tools: list[ToolSpec], user_input: str
) -> tuple[list[Message], list[ToolSpec]]:
    if strategy is ContextStrategy.summarized:
        return summarize(messages), tools
    if strategy is ContextStrategy.retrieved:
        return messages, retrieve_tools(tools, user_input, messages)
    return messages, tools
