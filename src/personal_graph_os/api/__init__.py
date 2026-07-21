"""HTTP API: the local UI's only path to the application services.

Every write route calls a service in `personal_graph_os.application`; nothing here talks
to a repository for a mutation. Reads may query a repository directly.
"""
