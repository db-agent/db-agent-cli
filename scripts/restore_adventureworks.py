"""Download AdventureWorks2017.bak once and restore it into the demo containers.

    python scripts/restore_adventureworks.py            # both containers from docker-compose.yml
    python scripts/restore_adventureworks.py 14331 14332

AdventureWorks is Microsoft's sample database (github.com/microsoft/sql-server-samples). It is
downloaded, not stored in this repo. Uses the host's pymssql, so the containers need no sqlcmd.
"""
import os
import sys
import time
import urllib.request
from pathlib import Path

import pymssql

ROOT = Path(__file__).resolve().parents[1]
BAK = ROOT / "data" / "AdventureWorks2017.bak"
URL = "https://github.com/Microsoft/sql-server-samples/releases/download/adventureworks/AdventureWorks2017.bak"
PW = os.environ.get("DBAGENT_DEMO_SA_PASSWORD", "DbAgent_Demo_2017!")
HOST = os.environ.get("DBAGENT_TEST_SERVER", "localhost")


def connect(port: int, wait: int = 600):
    deadline = time.time() + wait
    while True:
        try:
            return pymssql.connect(server=HOST, port=str(port), user="sa", password=PW, database="master", autocommit=True, login_timeout=10, timeout=0)
        except pymssql.Error as exc:
            if time.time() > deadline:
                raise SystemExit(f"port {port}: SQL Server did not come up: {exc}")
            time.sleep(5)


def main(ports: list[int]) -> None:
    BAK.parent.mkdir(exist_ok=True)
    if not BAK.exists() or BAK.stat().st_size == 0:
        print("downloading AdventureWorks2017.bak (about 48 MB)")
        urllib.request.urlretrieve(URL, BAK)
    for port in ports:
        print(f"== port {port}: waiting for SQL Server")
        conn = connect(port)
        cur = conn.cursor()
        cur.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(40))")
        print("   version", cur.fetchone()[0])
        cur.execute("SELECT DB_ID('AdventureWorks2017')")
        if cur.fetchone()[0]:
            print("   AdventureWorks2017 already there")
            continue
        cur.execute("RESTORE FILELISTONLY FROM DISK = '/backup/AdventureWorks2017.bak'")
        files = [(r[0], r[2]) for r in cur.fetchall()]  # logical name, type D/L
        moves = ", ".join(
            f"MOVE '{n}' TO '/var/opt/mssql/data/{n}.{'ldf' if t == 'L' else 'mdf' if n.endswith('2017') else 'ndf'}'" for n, t in files
        )
        print("   restoring")
        cur.execute(f"RESTORE DATABASE AdventureWorks2017 FROM DISK = '/backup/AdventureWorks2017.bak' WITH {moves}, REPLACE")
        while cur.nextset():
            pass
        print("   done")
        conn.close()


if __name__ == "__main__":
    main([int(p) for p in sys.argv[1:]] or [14331, 14332])
