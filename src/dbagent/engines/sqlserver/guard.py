"""The query guard. Every statement sent to any SQL Server passes through check().

Policy: one statement only. SELECT, or WITH ... SELECT, or EXEC of a stored procedure that
the caller explicitly allowed (validate --allow-exec). Anything else is refused, and so is
anything we cannot parse (fail closed).

Two independent checks must both pass:
  1. A text check on a sanitized copy (strings, comments and quoted names blanked out).
  2. A sqlglot parse (tsql dialect) with an AST walk.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp


class GuardError(Exception):
    """The statement is not allowed. The message never contains the statement text."""


_DENY_WORDS = (
    "INTO INSERT UPDATE DELETE MERGE DROP ALTER CREATE TRUNCATE GRANT REVOKE DENY "
    "BACKUP RESTORE SHUTDOWN KILL DBCC RECONFIGURE USE SET DECLARE WAITFOR BULK "
    "OPENROWSET OPENQUERY OPENDATASOURCE OPENXML READTEXT WRITETEXT UPDATETEXT "
    "EXEC EXECUTE SP_EXECUTESQL BEGIN COMMIT ROLLBACK GOTO RETURN PRINT RAISERROR THROW"
).split()
_DENY_RE = re.compile(r"\b(" + "|".join(_DENY_WORDS) + r")\b", re.IGNORECASE)
_XP_RE = re.compile(r"\bxp_\w*", re.IGNORECASE)
_FOUR_PART_RE = re.compile(r"[\w@#$]+(?:\.[\w@#$]*){3,}")
_PLACEHOLDER_RE = re.compile(r"%\((\w+)\)s")
_GO_RE = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)

_EXEC_RE = re.compile(
    r"^\s*EXEC(?:UTE)?\s+(?P<proc>[\w\.\[\]]+)\s*(?P<args>.*?)\s*;?\s*$", re.IGNORECASE | re.DOTALL
)
_EXEC_ARGS_RE = re.compile(r"^(@\w+\s*=\s*%\(\w+\)s(\s*,\s*@\w+\s*=\s*%\(\w+\)s)*)?$")


def _mask(segment: str) -> str:
    return "".join(ch if ch == "\n" else " " for ch in segment)


def sanitize(sql: str, preserve_length: bool = False, keep_strings: bool = False) -> str:
    """Blank out string literals, quoted names and comments so keywords inside them are ignored.

    Default mode: bracketed and double-quoted names become a plain word (_q_) so dotted chains
    still count, strings become '' and comments a space.
    preserve_length=True: every blanked character becomes a space (newlines kept), so offsets
    and line numbers in the result match the original text.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        two = sql[i : i + 2]
        start = i
        if two == "/*":
            depth = 1
            i += 2
            while i < n and depth:
                if sql[i : i + 2] == "/*":
                    depth += 1
                    i += 2
                elif sql[i : i + 2] == "*/":
                    depth -= 1
                    i += 2
                else:
                    i += 1
            out.append(_mask(sql[start:i]) if preserve_length else " ")
        elif two == "--":
            while i < n and sql[i] != "\n":
                i += 1
            out.append(_mask(sql[start:i]) if preserve_length else " ")
        elif c == "'":
            i += 1
            while i < n:
                if sql[i] == "'":
                    if sql[i : i + 2] == "''":
                        i += 2
                        continue
                    break
                i += 1
            i += 1
            out.append(sql[start:i] if keep_strings else (_mask(sql[start:i]) if preserve_length else "''"))
        elif c == "[":
            i += 1
            while i < n:
                if sql[i] == "]":
                    if sql[i : i + 2] == "]]":
                        i += 2
                        continue
                    break
                i += 1
            i += 1
            out.append(_mask(sql[start:i]) if preserve_length else "_q_")
        elif c == '"':
            i += 1
            while i < n and sql[i] != '"':
                i += 1
            i += 1
            out.append(_mask(sql[start:i]) if preserve_length else "_q_")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _normalize_proc(name: str) -> str:
    return name.replace("[", "").replace("]", "").strip().lower()


_BAD_NODES = tuple(
    getattr(exp, n)
    for n in (
        "Into", "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Command",
        "Set", "Use", "TruncateTable", "Grant", "Revoke", "Transaction", "Commit", "Rollback",
        "Declare", "Execute", "Copy", "LoadData", "Pragma",
    )
    if hasattr(exp, n)
)
_ROOT_OK = tuple(getattr(exp, n) for n in ("Select", "Union", "Subquery", "SetOperation") if hasattr(exp, n))


class QueryGuard:
    def __init__(self, allowed_procs: set[str] | None = None):
        self.allowed_procs = {_normalize_proc(p) for p in (allowed_procs or set())}

    def allow_proc(self, name: str) -> None:
        self.allowed_procs.add(_normalize_proc(name))

    def check(self, sql: str) -> None:
        """Raise GuardError unless sql is allowed. Returns None when allowed."""
        if not isinstance(sql, str) or not sql.strip():
            raise GuardError("empty statement")
        first_word = re.match(r"\s*(\w+)", sql)
        if first_word and first_word.group(1).upper() in ("EXEC", "EXECUTE"):
            self._check_exec(sql)
            return
        # Bound parameters (%(name)s) are values the driver sends separately. Check the statement
        # in its @name form, which the parser understands. '%%' is the driver's escape for '%'.
        sql = sql.replace("%%", "\x00")
        sql = _PLACEHOLDER_RE.sub(r"@\1", sql).replace("%s", "@p").replace("\x00", "%")
        clean = sanitize(sql)
        if _GO_RE.search(clean):
            raise GuardError("batch separator GO is not allowed")
        body = clean.strip().rstrip(";").strip()
        if ";" in body:
            raise GuardError("more than one statement")
        first = re.match(r"\s*(\w+)", body)
        word = first.group(1).upper() if first else ""
        if word not in ("SELECT", "WITH"):
            raise GuardError(f"only SELECT is allowed, got {word or 'unknown'}")
        hit = _DENY_RE.search(body)
        if hit:
            raise GuardError(f"keyword not allowed: {hit.group(1).upper()}")
        if _XP_RE.search(body):
            raise GuardError("extended stored procedures are not allowed")
        if _FOUR_PART_RE.search(body):
            raise GuardError("four-part names (linked servers) are not allowed")
        self._check_ast(sql)

    def _check_ast(self, sql: str) -> None:
        try:
            trees = sqlglot.parse(sql, read="tsql")
        except Exception as exc:  # noqa: BLE001 - any parser failure means refuse
            raise GuardError("could not verify the statement is read-only (parse failed)") from exc
        trees = [t for t in trees if t is not None]
        if len(trees) != 1:
            raise GuardError("more than one statement")
        tree = trees[0]
        if not isinstance(tree, _ROOT_OK):
            raise GuardError("statement is not a SELECT")
        for node in tree.walk():
            if isinstance(node, _BAD_NODES):
                raise GuardError(f"statement part not allowed: {type(node).__name__}")

    def _check_exec(self, sql: str) -> None:
        m = _EXEC_RE.match(sql)
        if not m:
            raise GuardError("EXEC form not recognised")
        proc = _normalize_proc(m.group("proc"))
        if proc not in self.allowed_procs:
            raise GuardError("stored procedure is not on the allowed list")
        if not _EXEC_ARGS_RE.match(m.group("args").strip()):
            raise GuardError("EXEC arguments must be named parameters (@name=%(name)s)")
        if _XP_RE.search(proc) or proc.startswith("xp_") or ".xp_" in proc:
            raise GuardError("extended stored procedures are not allowed")
