"""Every statement sent to a SQL warehouse goes through here first.

Allowed: one SELECT/WITH statement, SHOW SCHEMAS IN hive_metastore, SHOW TABLES IN hive_metastore.<schema>.
Anything else is refused, including statements that mention a write keyword anywhere outside literals.
"""

from __future__ import annotations

import re


class GuardError(Exception):
    pass


_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|create|alter|truncate|grant|revoke|deny|copy|optimize|vacuum|call|set|use|put|"
    r"msck|repair|refresh|analyze|cache|uncache|load|restore|undrop|overwrite|into|exec|execute)\b", re.I)
_SHOW = re.compile(r"^show\s+(schemas\s+in\s+hive_metastore|tables\s+in\s+hive_metastore\.`?[\w\-]+`?)$", re.I)


def _strip(sql: str) -> str:
    """Remove comments, string literals and quoted identifiers so keywords inside them do not count."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j < 0:
                raise GuardError("unterminated comment")
            i = j + 2
        elif c in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if sql[j] == "\\" and c != "`":
                    j += 2
                    continue
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:   # doubled quote
                        j += 2
                        continue
                    break
                j += 1
            else:
                raise GuardError("unterminated quote")
            out.append(" ")
            if c == "`":
                out[-1] = "x"   # keep backtick identifiers non-empty, so SHOW ... `schema` still matches
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def check(sql: str) -> None:
    body = _strip(sql).strip().rstrip(";").strip()
    if not body:
        raise GuardError("empty statement")
    if ";" in body:
        raise GuardError("only one statement is allowed")
    flat = re.sub(r"\s+", " ", body)
    if _SHOW.match(sql_without_comments(sql)):
        return
    first = flat.split(" ", 1)[0].lower()
    if first not in ("select", "with"):
        raise GuardError(f"only SELECT/WITH and SHOW ... IN hive_metastore are allowed, got {first.upper()}")
    bad = _FORBIDDEN.search(flat)
    if bad:
        raise GuardError(f"refused: {bad.group(0).upper()} is not allowed")


def sql_without_comments(sql: str) -> str:
    """Comments removed but literals and identifiers kept, for the exact SHOW forms."""
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    s = re.sub(r"--[^\n]*", " ", s)
    return re.sub(r"\s+", " ", s).strip().rstrip(";").strip()
