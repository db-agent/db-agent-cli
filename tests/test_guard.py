import pytest

from dbagent.engines.sqlserver.guard import GuardError, QueryGuard, sanitize

ALLOWED = [
    "SELECT 1",
    "select * from sys.tables;",
    "WITH x AS (SELECT 1 a) SELECT * FROM x",
    "SELECT REPLACE(name, 'a', 'b') FROM sys.objects",
    "SELECT [into], [select] FROM [dbo].[t]",
    "SELECT 'INSERT INTO x; DROP TABLE y' AS s",
    "SELECT * FROM a WITH (NOLOCK) WHERE x = 1 -- ; DROP TABLE a",
    "SELECT * FROM db.dbo.t",
    "SELECT /* DELETE */ 1",
    "SELECT SUM(x) FROM t GROUP BY y ORDER BY 1 OFFSET 0 ROWS FETCH NEXT 5 ROWS ONLY",
]
BLOCKED = [
    "", "   ",
    "SELECT * INTO t2 FROM t",
    "DROP TABLE t", "UPDATE t SET a = 1", "INSERT INTO t VALUES (1)", "DELETE FROM t", "TRUNCATE TABLE t",
    "ALTER TABLE t ADD c int", "CREATE TABLE t (a int)", "MERGE t USING s ON 1=1 WHEN MATCHED THEN DELETE;",
    "SELECT 1; DROP TABLE t",
    "SELECT 1\n;\nDROP TABLE t",
    "WITH x AS (SELECT 1 a) DELETE FROM t",
    "WITH x AS (SELECT 1 a) UPDATE t SET a = 1",
    "SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x;', 'SELECT 1')",
    "SELECT * FROM OPENQUERY(ls, 'SELECT 1')",
    "SELECT * FROM OPENDATASOURCE('SQLNCLI','Data Source=x').db.dbo.t",
    "SELECT * FROM ls.db.dbo.t",
    "SELECT xp_cmdshell('dir')",
    "EXEC sp_who", "EXECUTE sp_executesql N'DROP TABLE t'", "EXEC xp_cmdshell 'dir'",
    "DECLARE @a int; SELECT @a", "SET NOCOUNT ON", "USE master", "WAITFOR DELAY '00:00:05'",
    "GRANT SELECT ON t TO u", "BACKUP DATABASE x TO DISK='y'", "KILL 55", "DBCC CHECKDB",
    "BULK INSERT t FROM 'x'",
    "SELECT 1 /* x */; WAITFOR DELAY '00:00:05'",
    "\nGO\nSELECT 1",
    "SELECT 'unterminated",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql):
    QueryGuard().check(sql)


@pytest.mark.parametrize("sql", BLOCKED)
def test_blocked(sql):
    with pytest.raises(GuardError):
        QueryGuard().check(sql)


def test_error_message_never_contains_statement():
    with pytest.raises(GuardError) as e:
        QueryGuard().check("SELECT * INTO secret_table_name FROM t")
    assert "secret_table_name" not in str(e.value)


def test_exec_only_for_allowed_procedures_with_named_parameters():
    g = QueryGuard({"dbo.uspGood"})
    g.check("EXEC dbo.uspGood @a=%(a)s, @b=%(b)s")
    g.check("EXEC [dbo].[uspGood]")
    for bad in ["EXEC dbo.uspOther @a=%(a)s", "EXEC dbo.uspGood @a=1", "EXEC dbo.uspGood @a=%(a)s; DROP TABLE t",
                "EXEC dbo.uspGood 5", "EXEC dbo.uspGood @a=%(a)s, @b='x'"]:
        with pytest.raises(GuardError):
            g.check(bad)


def test_sanitize_preserves_length_and_blanks_strings_and_comments():
    s = "SELECT 'a--b', [c;d] -- tail\n/* x */ FROM t"
    out = sanitize(s, preserve_length=True)
    assert len(out) == len(s) and out.count("\n") == s.count("\n")
    assert "a--b" not in out and "c;d" not in out and "tail" not in out


def test_bound_parameters_are_checked_in_their_at_form():
    g = QueryGuard()
    g.check("SELECT a FROM t WHERE d >= %(Start)s AND n LIKE '%%x%%' AND k = %(Top)s")
    g.check("SELECT n FROM sys.objects WHERE object_id = OBJECT_ID(%s)")
    for bad in ["SELECT %(a)s; DROP TABLE t", "SELECT * INTO x FROM t WHERE a = %(a)s", "UPDATE t SET a = %(a)s",
                "SELECT 1 WHERE a = %(a)s; EXEC sp_who", "SELECT * INTO t FROM x WHERE a = %s"]:
        with pytest.raises(GuardError):
            g.check(bad)
