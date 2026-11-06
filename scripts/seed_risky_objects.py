"""Create a database named RiskLab full of legacy objects, to check the risk rules on a real engine.

    DBAGENT_SEED_SERVER=localhost DBAGENT_SEED_PORT=14331 DBAGENT_SEED_USER=sa DBAGENT_SEED_PASSWORD=... \
        python scripts/seed_risky_objects.py [--clr-hex FILE]

Test setup code: it uses a raw pymssql connection with a privileged login, not the kit. Items the engine does not
support (SQL Server Agent on a container without it, CLR on Linux) are skipped with a message.
"""
import argparse
import os
import sys

import pymssql

BATCHES = [
    ("table with deprecated types", """CREATE TABLE dbo.Legacy (id int IDENTITY PRIMARY KEY, notes text, memo ntext, pic image, ok varchar(20))"""),
    ("stand-alone default and rule", "CREATE DEFAULT dbo.df_zero AS 0"),
    ("stand-alone rule", "CREATE RULE dbo.rl_pos AS @x > 0"),
    ("proc: SET ROWCOUNT", "CREATE PROCEDURE dbo.p_rowcount AS BEGIN SET ROWCOUNT 10; SELECT id FROM dbo.Legacy; SET ROWCOUNT 0 END"),
    ("proc: GROUP BY ALL", "CREATE PROCEDURE dbo.p_groupall AS SELECT ok, COUNT(*) c FROM dbo.Legacy GROUP BY ALL ok"),
    ("proc: weak hash", "CREATE PROCEDURE dbo.p_md5 AS SELECT HASHBYTES('MD5', 'x') h"),
    ("proc: table hint without WITH", "CREATE PROCEDURE dbo.p_hint AS SELECT id FROM dbo.Legacy (NOLOCK)"),
    ("proc: compatibility view", "CREATE PROCEDURE dbo.p_sysobjects AS SELECT name FROM sysobjects"),
    ("proc: DBCC DBREINDEX", "CREATE PROCEDURE dbo.p_dbcc AS DBCC DBREINDEX ('dbo.Legacy')"),
    ("proc: SET FMTONLY", "CREATE PROCEDURE dbo.p_fmtonly AS BEGIN SET FMTONLY ON; SELECT id FROM dbo.Legacy; SET FMTONLY OFF END"),
    ("proc: xp_cmdshell", "CREATE PROCEDURE dbo.p_cmd AS EXEC master..xp_cmdshell 'dir'"),
    ("proc: cross-database reference", "CREATE PROCEDURE dbo.p_xdb AS SELECT TOP 1 ProductID FROM {xdb}.Production.Product"),
    ("proc: encrypted", "CREATE PROCEDURE dbo.p_secret WITH ENCRYPTION AS SELECT 1 x"),
    ("synonym to another database", "CREATE SYNONYM dbo.syn_xdb FOR {xdb}.Production.Product"),
    ("service broker", "CREATE MESSAGE TYPE [//RiskLab/Msg] VALIDATION = NONE"),
    ("service broker contract", "CREATE CONTRACT [//RiskLab/Contract] ([//RiskLab/Msg] SENT BY INITIATOR)"),
    ("service broker queue", "CREATE QUEUE dbo.RiskQueue"),
    ("service broker service", "CREATE SERVICE [//RiskLab/Service] ON QUEUE dbo.RiskQueue ([//RiskLab/Contract])"),
]
ANSI_OFF = ("proc: ANSI_NULLS OFF", "SET ANSI_NULLS OFF", "CREATE PROCEDURE dbo.p_ansi AS SELECT 1 x", "SET ANSI_NULLS ON")


def connect(db="master"):
    return pymssql.connect(server=os.environ["DBAGENT_SEED_SERVER"], port=os.environ.get("DBAGENT_SEED_PORT", "1433"),
                           user=os.environ["DBAGENT_SEED_USER"], password=os.environ["DBAGENT_SEED_PASSWORD"],
                           database=db, autocommit=True, login_timeout=30, timeout=0)


def run(cur, label, sql):
    try:
        cur.execute(sql)
        print(f"  ok      {label}")
    except Exception as exc:  # noqa: BLE001
        print(f"  SKIPPED {label}: {str(exc)[:110]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clr-hex", help="file holding the hex of a compiled .NET assembly (0x...)")
    ap.add_argument("--clr-path", help="path, on the SQL Server machine, of a compiled .NET assembly (.dll)")
    a = ap.parse_args()
    c = connect()
    cur = c.cursor()
    cur.execute("IF DB_ID('RiskLab') IS NOT NULL BEGIN ALTER DATABASE RiskLab SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE RiskLab END")
    cur.execute("CREATE DATABASE RiskLab")
    d = connect("RiskLab").cursor()
    cur.execute("SELECT TOP 1 name FROM sys.databases WHERE name LIKE 'AdventureWorks%' ORDER BY name")
    row = cur.fetchone()
    xdb = row[0] if row else "master"
    for label, sql in BATCHES:
        run(d, label, sql.replace("{xdb}", xdb))
    label, pre, create, post = ANSI_OFF
    d.execute(pre)
    run(d, label, create)
    d.execute(post)
    # linked server and a synonym through it (drop leftovers from an earlier run)
    run(cur, "drop old linked server", "IF EXISTS (SELECT 1 FROM sys.servers WHERE name='LEGACY_LINK') EXEC master.dbo.sp_dropserver 'LEGACY_LINK', 'droplogins'")
    run(cur, "drop old agent job", "IF EXISTS (SELECT 1 FROM msdb.dbo.sysjobs WHERE name='RiskLab nightly') EXEC msdb.dbo.sp_delete_job @job_name='RiskLab nightly'")
    run(cur, "linked server (SQLOLEDB)", "EXEC master.dbo.sp_addlinkedserver @server='LEGACY_LINK', @srvproduct='', @provider='SQLOLEDB', @datasrc='remote.example.local'")
    run(d, "synonym through linked server", "CREATE SYNONYM dbo.syn_link FOR LEGACY_LINK.Sales.dbo.Orders")
    if a.clr_hex or a.clr_path:
        hexv = open(a.clr_hex).read().strip() if a.clr_hex else "'" + a.clr_path.replace("'", "''") + "'"
        run(cur, "enable clr", "EXEC sp_configure 'show advanced options', 1; RECONFIGURE; EXEC sp_configure 'clr enabled', 1; RECONFIGURE")
        run(cur, "trustworthy", "ALTER DATABASE RiskLab SET TRUSTWORTHY ON")
        run(d, "CLR assembly", f"CREATE ASSEMBLY RiskClr FROM {hexv} WITH PERMISSION_SET = SAFE")
    # an Agent job with a deprecated step
    run(cur, "agent job", """USE msdb;
EXEC dbo.sp_add_job @job_name='RiskLab nightly';
EXEC dbo.sp_add_jobstep @job_name='RiskLab nightly', @step_name='old code', @subsystem='TSQL', @database_name='RiskLab',
     @command='SET ROWCOUNT 5; SELECT name FROM sysobjects; SELECT HASHBYTES(''SHA1'', ''x'')';
EXEC dbo.sp_add_jobserver @job_name='RiskLab nightly'""")
    print("done")


if __name__ == "__main__":
    sys.exit(main())
