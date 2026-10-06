"""
Cockpit V4: one Opus analyst, governed tools, a live execution trace.

What is different from V3, and why it is a separate package
-----------------------------------------------------------
V3 spends four model calls before the first one that could answer anything:
two Sonnet preprocessing passes, an ownership gate, then a plan. V4 spends
one. The analyst receives the user's original wording and a small factual
context, and its FIRST generation either answers or asks for a tool. Nothing
here decides on its behalf what the question means, which fields it needs,
or how to repair a query that failed -- those are the analyst's, and this
package's job is to be a safe messenger: supply authorized context, validate,
execute, account, persist, and stream what actually happened.

`backend/cockpit_agentic/` is untouched. V4 imports from it only where the V3
code is genuinely correct and shared -- the catalog, the SQL session and its
guards, the scope and credential rules, the token counter, the release store.
Those are adapters, listed in docs/cockpit_v4/SOURCE_MAPPING.md, and none of
them is modified from here.
"""

from __future__ import annotations

#: The one authorized analytical domain. Not configurable at runtime.
DOMAIN = "corporate_cockpit"

#: Bumped when the persisted event rows change shape.
EVENT_SCHEMA_VERSION = "v4.1"

#: Bumped when the analyst's system prompt changes meaning.
PROMPT_VERSION = "v4.1"

#: Bumped when the four tool contracts change shape.
TOOL_CONTRACT_VERSION = "v4.1"

STANDARD = "standard"
DEEP = "deep"
MODES: tuple[str, ...] = (STANDARD, DEEP)

__all__ = ["DOMAIN", "EVENT_SCHEMA_VERSION", "PROMPT_VERSION",
           "TOOL_CONTRACT_VERSION", "STANDARD", "DEEP", "MODES"]
