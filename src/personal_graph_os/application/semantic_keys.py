"""Immutable semantic role identifiers for the Resource -> Takeaway -> Decision -> Task ->
Implementation workflow (ST-04).

Both `default_schema.py` (fresh workspaces) and `semantic_schema.py` (upgrading an existing
workspace created before these roles existed) key off these same constants, so the workflow
(ST-04.4) can always find its five node types and four edge types by role rather than by a
user-editable display name.
"""

from __future__ import annotations

RESOURCE_NODE_TYPE_KEY = "resource"
TAKEAWAY_NODE_TYPE_KEY = "takeaway"
DECISION_NODE_TYPE_KEY = "decision"
TASK_NODE_TYPE_KEY = "task"
IMPLEMENTATION_NODE_TYPE_KEY = "implementation"
# The single backing node type for every `WorkItem` (Epic/Story/Task) -- EP-2026-012 ST-05,
# distinct from `TASK_NODE_TYPE_KEY` above (the older Resource -> Takeaway -> Decision -> Task ->
# Implementation guided-workflow step, an unrelated concept that predates the agentic
# Epic/Story/Task work hierarchy).
WORK_ITEM_NODE_TYPE_KEY = "work_item"

RESOURCE_YIELDS_TAKEAWAY_EDGE_KEY = "resource_yields_takeaway"
TAKEAWAY_INFORMS_DECISION_EDGE_KEY = "takeaway_informs_decision"
DECISION_PRODUCES_TASK_EDGE_KEY = "decision_produces_task"
TASK_IMPLEMENTED_BY_EDGE_KEY = "task_implemented_by"
