from __future__ import annotations

from personal_graph_os.domain.search import compile_fts5_query


def test_compile_fts5_query_returns_none_for_blank_input() -> None:
    assert compile_fts5_query("") is None
    assert compile_fts5_query("   ") is None


def test_compile_fts5_query_wraps_each_token_as_a_prefix_phrase() -> None:
    assert compile_fts5_query("attention transformer") == '"attention"* AND "transformer"*'


def test_compile_fts5_query_escapes_embedded_double_quotes() -> None:
    compiled = compile_fts5_query('say "hi"')
    assert compiled == '"say"* AND """hi"""*'


def test_compile_fts5_query_neutralizes_fts5_operator_syntax() -> None:
    """A literal search for `title:foo OR NOT (bar)` must not be interpreted as FTS5 query
    syntax: every token is quoted, so colons/parens/boolean keywords are searched literally."""
    compiled = compile_fts5_query("title:foo OR NOT (bar)")
    assert compiled == '"title:foo"* AND "OR"* AND "NOT"* AND "(bar)"*'
