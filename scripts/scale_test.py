"""Generate a large synthetic estate to time the kit: tables, procedures, views and RDL files.

    DBAGENT_SEED_SERVER=localhost DBAGENT_SEED_PORT=14331 DBAGENT_SEED_USER=sa DBAGENT_SEED_PASSWORD=... \
        python scripts/scale_test.py --tables 5000 --procs 2000 --views 1000 --reports 3000 --rdl-out /tmp/scale-rdl

Creates the database ScaleLab (dropped first). Test setup code with a privileged login, not the kit.
"""
import argparse
import os
import random
from pathlib import Path

import pymssql


def connect(db="master"):
    return pymssql.connect(server=os.environ["DBAGENT_SEED_SERVER"], port=os.environ.get("DBAGENT_SEED_PORT", "1433"),
                           user=os.environ["DBAGENT_SEED_USER"], password=os.environ["DBAGENT_SEED_PASSWORD"],
                           database=db, autocommit=True, login_timeout=30, timeout=0)


def batches(stmts, n=100):
    for i in range(0, len(stmts), n):
        yield stmts[i:i + n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", type=int, default=5000)
    ap.add_argument("--procs", type=int, default=2000)
    ap.add_argument("--views", type=int, default=1000)
    ap.add_argument("--reports", type=int, default=3000)
    ap.add_argument("--rdl-out", default="scale-rdl")
    ap.add_argument("--template", default="examples/rdl/Sales/TopProducts.rdl")
    a = ap.parse_args()
    random.seed(7)
    c = connect()
    cur = c.cursor()
    cur.execute("IF DB_ID('ScaleLab') IS NOT NULL BEGIN ALTER DATABASE ScaleLab SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE ScaleLab END")
    cur.execute("CREATE DATABASE ScaleLab")
    d = connect("ScaleLab").cursor()
    for i in range(0, a.tables, 100):
        d.execute(";".join(f"CREATE TABLE dbo.T{j} (id int IDENTITY PRIMARY KEY, a varchar(50), b datetime, c decimal(12,2), d nvarchar(100))"
                           for j in range(i, min(i + 100, a.tables))))
        if (i // 100) % 10 == 0:
            print(f"  tables {i}", flush=True)
    for j in range(a.views):
        d.execute(f"CREATE VIEW dbo.V{j} AS SELECT id, a, c FROM dbo.T{j % a.tables}")
    for j in range(a.procs):
        t = j % a.tables
        d.execute(f"CREATE PROCEDURE dbo.P{j} AS SELECT id, a FROM dbo.T{t} (NOLOCK) WHERE c > 0 " + ("; SET ROWCOUNT 5" if j % 7 == 0 else ""))
    d.execute("SELECT COUNT(*) FROM sys.tables"); print("tables:", d.fetchone()[0])
    out = Path(a.rdl_out)
    tpl = Path(a.template).read_text(encoding="utf-8-sig")
    for j in range(a.reports):
        folder = out / f"Dept{j % 40}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"Report{j}.rdl").write_text(tpl.replace("SalesOrderHeader", f"T{j % a.tables}") if "SalesOrderHeader" in tpl else tpl, encoding="utf-8")
    print("rdl files:", a.reports, "in", out)


if __name__ == "__main__":
    main()
