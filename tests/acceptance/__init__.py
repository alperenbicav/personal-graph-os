"""ST-09 system-acceptance fixtures and harness support.

Everything here drives the real FastAPI app (and, for MCP coverage, the real MCP SDK
client) through its public HTTP/MCP surface — never raw SQL — so the fixtures it builds
exercise the same schema, audit, search, and file invariants a real user would. Modules
are shared between pytest (`test_functional_fixture.py`, `test_scale_fixture.py`) and the
standalone harness command (`scripts/system_pilot.py`).
"""

from __future__ import annotations
