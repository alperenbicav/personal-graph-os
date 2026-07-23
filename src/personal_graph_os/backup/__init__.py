"""Consistent, verifiable backup/restore of a whole workspace directory (ST-07.5).

A composition-root package, like `personal_graph_os.api`: free to wire concrete SQLite
repositories and the local managed-file store directly, unlike `domain`/`application`.
"""

from __future__ import annotations
