"""Application layer: repository protocols and use-case services.

Every caller (future UI, MCP tool, or discovery agent) mutates the graph exclusively
through these services. No caller is permitted to write to a repository directly.
"""
