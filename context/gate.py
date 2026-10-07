# context/gate.py — every tool/DB/MCP result passes through here before it reaches a prompt. Results are untrusted
# and unbounded, so they are capped, trimmed to the columns that matter, and always say when something was left out.
# guard_sql is the read-only door for any model-written SQL.
from __future__ import annotations

import re
from typing import Any

from llm.client import ModelProfile
from llm.tokens import DEFAULT_CHARS_PER_TOKEN, DEFAULT_SAFETY_MARGIN, estimate_tokens

CELL_MAX_CHARS = 200
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


# ---------- size caps ----------

def gate_text(text: str, max_tokens: int, profile: ModelProfile | None = None) -> str:
    """Cut `text` to at most `max_tokens` (the note included), saying how much was removed. Pass the model `profile`
    so the count matches the packer's; without one the default ratio is used."""
    if estimate_tokens(text, profile) <= max_tokens:
        return text

    ratio = profile.chars_per_token if profile else DEFAULT_CHARS_PER_TOKEN
    margin = profile.safety_margin if profile else DEFAULT_SAFETY_MARGIN
    keep = max(int(max_tokens * ratio / margin), 0)
    while keep > 0:
        out = text[:keep].rstrip() + f"\n[truncated: {len(text) - keep} more characters]"
        if estimate_tokens(out, profile) <= max_tokens:
            return out
        keep = int(keep * 0.9) if keep > 10 else 0   # the note itself costs tokens, so back off until it all fits
    return f"[truncated: {len(text)} characters, nothing fits]"


def _cell(value: Any) -> str:
    text = "" if value is None else str(value).replace("\n", " ")
    return text if len(text) <= CELL_MAX_CHARS else text[: CELL_MAX_CHARS - 1] + "…"


def gate_rows(rows: list[dict[str, Any]], max_tokens: int, keep_columns: list[str] | None = None) -> str:
    """Rows as a compact table: only `keep_columns` (in that order; default every column), whole rows only, and a
    note with the number left out ("37 more rows, narrow the query") when they do not all fit."""
    if not rows:
        return "(no rows)"

    columns = keep_columns if keep_columns is not None else list(dict.fromkeys(c for row in rows for c in row))
    header = " | ".join(columns)
    lines = [" | ".join(_cell(row.get(c)) for c in columns) for row in rows]

    def note(left: int) -> str:
        return f"[{left} more row{'' if left == 1 else 's'}, narrow the query]"

    # Largest prefix of rows that fits together with the header and, if rows are left out, the note.
    shown = len(lines)
    while True:
        out = "\n".join([header, *lines[:shown]] + ([note(len(lines) - shown)] if shown < len(lines) else []))
        if estimate_tokens(out) <= max_tokens or shown == 0:
            return out
        shown -= 1


# ---------- read-only SQL ----------

class UnsafeSQL(ValueError):
    """The SQL is not a single read-only SELECT."""


_FORBIDDEN_WORDS = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call|do|execute|set|reset|vacuum|"
    r"analyze|analyse|reindex|cluster|lock|comment|refresh|listen|notify|unlisten|prepare|deallocate|discard|"
    r"explain|into|fetch|begin|commit|rollback|savepoint|security|"
    r"set_config|nextval|setval|dblink\w*|lo_\w+|pg_\w+|"
    r"query_to_xml\w*|cursor_to_xml|table_to_xml\w*|schema_to_xml\w*|database_to_xml\w*)\b",
    re.IGNORECASE,
)
_ROW_LOCK = re.compile(r"\bfor\s+(no\s+key\s+update|update|share|key\s+share)\b", re.IGNORECASE)
_LIMIT = re.compile(r"\blimit\b(?:\s+(\d+|all)\b)?", re.IGNORECASE)


def _mask(sql: str) -> str:
    """`sql` with the insides of string literals and quoted identifiers blanked out (same length, so positions match),
    refusing anything the scanner cannot reason about: comments, dollar quotes, E'..' / U&'..' strings, open quotes."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"":
            if ch == "'" and i and sql[i - 1] in "eEu&" and re.search(r"(?<![\w$])(e|u&)$", sql[:i], re.IGNORECASE):
                raise UnsafeSQL("escape-style strings are not allowed")
            j = i + 1
            while True:
                if j >= n:
                    raise UnsafeSQL("unterminated quote")
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:    # a doubled quote is an escaped quote
                        j += 2
                        continue
                    break
                j += 1
            out.append(ch + " " * (j - i - 1) + ch)
            i = j + 1
            continue
        if sql.startswith("--", i) or sql.startswith("/*", i):
            raise UnsafeSQL("comments are not allowed")
        if ch == "$":
            raise UnsafeSQL("dollar-quoting is not allowed")
        out.append(ch)
        i += 1
    return "".join(out)


def _depths(masked: str) -> list[int]:
    depth, out = 0, []
    for ch in masked:
        if ch == ")":
            depth -= 1
        out.append(depth)
        if ch == "(":
            depth += 1
    return out


def guard_sql(sql: str, *, default_limit: int = DEFAULT_LIMIT, max_limit: int = MAX_LIMIT) -> str:
    """Return `sql` only if it is a single read-only SELECT (or WITH ... SELECT), with a LIMIT that is present and no
    larger than `max_limit`. Anything else raises UnsafeSQL. This is a gate for model-written SQL; it complements, and
    does not replace, running it on a read-only database role."""
    sql = sql.strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if not sql:
        raise UnsafeSQL("empty query")

    masked = _mask(sql)
    if ";" in masked:
        raise UnsafeSQL("only one statement is allowed")
    if not re.match(r"(select|with)\b", masked, re.IGNORECASE):
        raise UnsafeSQL("only SELECT queries are allowed")
    if (bad := _FORBIDDEN_WORDS.search(masked)) or (bad := _ROW_LOCK.search(masked)):
        raise UnsafeSQL(f"'{bad.group(0).strip()}' is not allowed in a read-only query")

    depths = _depths(masked)
    top = [m for m in _LIMIT.finditer(masked) if depths[m.start()] == 0]
    if not top:
        return f"{sql} LIMIT {default_limit}"

    value = top[-1].group(1)
    if value is None:
        raise UnsafeSQL("LIMIT must be a plain number")
    number = max_limit if value.lower() == "all" else min(int(value), max_limit)
    start, end = top[-1].span(1)
    return sql[:start] + str(number) + sql[end:]
