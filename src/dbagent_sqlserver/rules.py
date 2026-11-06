"""Upgrade-risk rules for T-SQL module code.

Built from Microsoft's lists, fetched 2026-11-05:
  DEPRECATED: Deprecated Database Engine Features in SQL Server 2016 (the cumulative baseline)
  DISCONTINUED: Discontinued Database Engine Functionality in SQL Server
Microsoft's pages for 2017, 2019 and 2022 add a few items (for example Distributed Replay, Machine
Learning Server). They are not detectable from module code and are not in this table.
See docs/risk-rules.md for the list and what each rule is based on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DOC_DEPRECATED = (
    "https://learn.microsoft.com/en-us/previous-versions/sql/database-engine/"
    "deprecated-database-engine-features-in-sql-server-2016"
)
DOC_DISCONTINUED = "https://learn.microsoft.com/en-us/sql/database-engine/discontinued-database-engine-functionality-in-sql-server"
DOC_COMPAT = "https://learn.microsoft.com/en-us/sql/t-sql/statements/alter-database-transact-sql-compatibility-level"
DOC_AZURE_DIFF = "https://learn.microsoft.com/en-us/azure/azure-sql/database/transact-sql-tsql-differences-sql-server"

# Compatibility level for each major version.
COMPAT_BY_MAJOR = {13: 130, 14: 140, 15: 150, 16: 160, 17: 170}


@dataclass(frozen=True)
class CodeRule:
    id: str
    severity: str  # high | medium | low
    title: str
    pattern: re.Pattern
    replacement: str
    doc: str = DOC_DEPRECATED
    needs_strings: bool = False  # the rule looks inside string literals (for example HASHBYTES('MD5', ...))


def _r(p: str) -> re.Pattern:
    return re.compile(p, re.IGNORECASE)


_SYS_PROCS = (
    "sp_addlogin sp_droplogin sp_adduser sp_dropuser sp_grantdbaccess sp_revokedbaccess sp_addrole "
    "sp_droprole sp_password sp_approlepassword sp_changeobjectowner sp_defaultdb sp_defaultlanguage "
    "sp_denylogin sp_grantlogin sp_revokelogin sp_addapprole sp_dropapprole sp_change_users_login "
    "sp_depends sp_renamedb sp_dbcmptlevel sp_attach_db sp_attach_single_file_db sp_addtype sp_droptype "
    "sp_bindefault sp_unbindefault sp_bindrule sp_unbindrule sp_helpdevice sp_lock sp_indexoption "
    "sp_dbremove sp_addremotelogin sp_addserver sp_dropremotelogin sp_helpremotelogin sp_remoteoption "
    "sp_resetstatus sp_bindsession sp_getbindtoken sp_addextendedproc sp_dropextendedproc "
    "sp_helpextendedproc xp_grantlogin xp_revokelogin xp_loginconfig sp_certify_removable "
    "sp_create_removable sp_srvrolepermission sp_dbfixedrolepermission sp_trace_create sp_trace_setevent "
    "sp_trace_setfilter sp_trace_setstatus sp_db_vardecimal_storage_format "
    "sp_estimated_rowsize_reduction_for_vardecimal"
).split()

_COMPAT_VIEWS = (
    "sysaltfiles syscacheobjects syscolumns syscomments sysconfigures sysconstraints syscurconfigs "
    "sysdatabases sysdepends sysdevices sysfilegroups sysfiles sysforeignkeys sysfulltextcatalogs "
    "sysindexes sysindexkeys syslockinfo syslogins sysmembers sysmessages sysobjects sysoledbusers "
    "sysopentapes sysperfinfo syspermissions sysprocesses sysprotects sysreferences sysremotelogins "
    "sysservers systypes sysusers"
).split()

_HINTS = (
    "NOLOCK|READUNCOMMITTED|HOLDLOCK|UPDLOCK|ROWLOCK|TABLOCKX|TABLOCK|READPAST|NOWAIT|XLOCK|PAGLOCK|"
    "SERIALIZABLE|REPEATABLEREAD|READCOMMITTED"
)

CODE_RULES: list[CodeRule] = [
    CodeRule("dep-sp-removed-security-and-admin", "medium",
             "Deprecated system stored procedure",
             _r(r"\b(" + "|".join(_SYS_PROCS) + r")\b"),
             "Use the current statement (CREATE LOGIN, CREATE USER, ALTER DATABASE, sys.dm_* views, Extended Events). See the replacement column in Microsoft's list."),
    CodeRule("dep-compat-views", "medium",
             "Compatibility system view (sysobjects, syscolumns and similar)",
             _r(r"(?<![\w.])(?:(?:master|msdb|tempdb|\w+)\.)?(?:dbo\.|sys\.)?(" + "|".join(_COMPAT_VIEWS) + r")\b"),
             "Use catalog views (sys.objects, sys.columns, sys.indexes, ...)."),
    CodeRule("dep-set-rowcount", "low",
             "SET ROWCOUNT (deprecated for INSERT, UPDATE and DELETE)",
             _r(r"\bSET\s+ROWCOUNT\b"), "Use TOP. SET ROWCOUNT 0 only resets the setting; review before changing."),
    CodeRule("dep-set-fmtonly", "medium", "SET FMTONLY",
             _r(r"\bSET\s+FMTONLY\b"), "Use sp_describe_first_result_set or sys.dm_exec_describe_first_result_set."),
    CodeRule("dep-group-by-all", "medium", "GROUP BY ALL",
             _r(r"\bGROUP\s+BY\s+ALL\b"), "Rewrite with a derived table or UNION."),
    CodeRule("dep-set-ansi-off", "medium", "SET ANSI_NULLS / ANSI_PADDING / CONCAT_NULL_YIELDS_NULL OFF, SET OFFSETS",
             _r(r"\bSET\s+(ANSI_NULLS|ANSI_PADDING|CONCAT_NULL_YIELDS_NULL)\s+OFF\b|\bSET\s+OFFSETS\b"),
             "These options are always ON in a future version. Test the code with them ON."),
    CodeRule("dep-table-hint-without-with", "low", "Table hint without the WITH keyword",
             _r(r"(\bWITH\s*)?\(\s*(?:" + _HINTS + r")\b"),
             "Write WITH (NOLOCK), not (NOLOCK)."),
    CodeRule("dep-textptr", "medium", "READTEXT / WRITETEXT / UPDATETEXT / TEXTPTR / TEXTVALID",
             _r(r"\b(READTEXT|WRITETEXT|UPDATETEXT|TEXTPTR|TEXTVALID)\b"), "Use varchar(max), nvarchar(max), varbinary(max)."),
    CodeRule("dep-dbcc", "low", "DBCC DBREINDEX / INDEXDEFRAG / SHOWCONTIG / PINTABLE / UNPINTABLE",
             _r(r"\bDBCC\s+(DBREINDEX|INDEXDEFRAG|SHOWCONTIG|PINTABLE|UNPINTABLE)\b"),
             "ALTER INDEX ... REBUILD / REORGANIZE, sys.dm_db_index_physical_stats."),
    CodeRule("dep-functions", "low", "Deprecated function",
             _r(r"\b(fn_get_sql|USER_ID|PERMISSIONS|INDEXKEY_PROPERTY|fn_virtualservernodes|fn_servershareddrives)\s*\("),
             "fn_get_sql -> sys.dm_exec_sql_text, USER_ID -> DATABASE_PRINCIPAL_ID, PERMISSIONS -> sys.fn_my_permissions."),
    CodeRule("dep-colon-colon-function", "low", "'::' function-calling syntax",
             _r(r"::\s*fn_\w+"), "Use SELECT ... FROM sys.fn_name()."),
    CodeRule("dep-setuser", "medium", "SETUSER", _r(r"\bSETUSER\b"), "Use EXECUTE AS."),
    CodeRule("dep-grant-all", "low", "GRANT / DENY / REVOKE ALL",
             _r(r"\b(GRANT|DENY|REVOKE)\s+ALL\b"), "Grant specific permissions."),
    CodeRule("dep-weak-crypto", "high", "RC4 / DESX encryption or MD2, MD4, MD5, SHA, SHA1 hashing",
             _r(r"\bALGORITHM\s*=\s*(RC4|RC4_128|DESX)\b|\bHASHBYTES\s*\(\s*'(MD2|MD4|MD5|SHA|SHA1)'"),
             "Use AES for encryption and SHA2_256 or SHA2_512 for hashing.", needs_strings=True),
    CodeRule("dep-xmldata", "low", "FOR XML ... XMLDATA", _r(r"\bXMLDATA\b"), "Use XSD generation."),
    CodeRule("dep-sys-views", "low", "Deprecated catalog view",
             _r(r"\bsys\.(sql_dependencies|numbered_procedures|numbered_procedure_parameters|traces|trace_events|trace_event_bindings|trace_categories|trace_columns|trace_subclass_values|soap_endpoints|endpoint_webmethods)\b"),
             "sys.sql_dependencies -> sys.sql_expression_dependencies; SQL Trace views -> Extended Events."),
    CodeRule("dep-identitycol-rowguidcol", "low", "IDENTITYCOL / ROWGUIDCOL as a column name",
             _r(r"\b(IDENTITYCOL|ROWGUIDCOL)\b"), "Use $identity and $rowguid."),
    CodeRule("dep-create-default-rule", "low", "CREATE DEFAULT / CREATE RULE",
             _r(r"\bCREATE\s+(DEFAULT|RULE)\b|\bDROP\s+(DEFAULT|RULE)\b"), "Use DEFAULT and CHECK constraints."),
    CodeRule("dep-raiserror-old", "low", "Old RAISERROR syntax without parentheses",
             _r(r"\bRAISERROR\s+\d+\s+['@]"), "Use RAISERROR (msg, severity, state) or THROW. Not on Microsoft's deprecated list; removed earlier."),
    CodeRule("dep-sqloledb", "medium", "SQLOLEDB provider",
             _r(r"\bSQLOLEDB\b"), "Use Microsoft OLE DB Driver for SQL Server (MSOLEDBSQL)."),
]


def scan_code(code: str) -> list[tuple[CodeRule, int, str]]:
    """Return (rule, line number, matched text) for every rule hit. Line numbers start at 1.

    Strings, comments and quoted names are blanked first, so dynamic SQL held in a string is
    not scanned (documented limit).
    """
    from .guard import sanitize

    clean = sanitize(code, preserve_length=True)
    with_strings = sanitize(code, preserve_length=True, keep_strings=True)
    hits: list[tuple[CodeRule, int, str]] = []
    for rule in CODE_RULES:
        text = with_strings if rule.needs_strings else clean
        for m in rule.pattern.finditer(text):
            if rule.id == "dep-table-hint-without-with" and m.group(1):
                continue  # WITH (NOLOCK) is the correct form
            line = clean.count("\n", 0, m.start()) + 1
            hits.append((rule, line, m.group(0).strip()))
    return hits
