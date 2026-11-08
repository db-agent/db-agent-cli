"""Integration tests need the demo containers: docker compose up -d && scripts/restore_adventureworks.sh

They are skipped unless DBAGENT_TEST_SERVER is set (for example DBAGENT_TEST_SERVER=localhost).
Setup and teardown in these tests use a raw pymssql connection as sa, which is test code, not the kit.
"""
import os

import pymssql
import pytest

from db_agent.engines.sqlserver.db import ConnectionConfig

HOST = os.environ.get("DBAGENT_TEST_SERVER")
PASSWORD = os.environ.get("DBAGENT_DEMO_SA_PASSWORD", "DbAgent_Demo_2017!")
SRC_PORT = int(os.environ.get("DBAGENT_TEST_SRC_PORT", 14331))
TGT_PORT = int(os.environ.get("DBAGENT_TEST_TGT_PORT", 14332))

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(config, items):
    if HOST:
        return
    skip = pytest.mark.skip(reason="set DBAGENT_TEST_SERVER to run integration tests")
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)


def cfg(port, user="sa", password=PASSWORD, database="", **kw) -> ConnectionConfig:
    return ConnectionConfig(server=HOST or "localhost", port=port, user=user, password=password, database=database,
                            encrypt=False, login_timeout=60, query_timeout=120, **kw)


@pytest.fixture(scope="session")
def src():
    return cfg(SRC_PORT)


@pytest.fixture(scope="session")
def tgt():
    return cfg(TGT_PORT)


def admin(port, database="master"):
    conn = pymssql.connect(server=HOST, port=str(port), user="sa", password=PASSWORD, database=database, autocommit=True, login_timeout=60)
    return conn


@pytest.fixture()
def sa_exec():
    """Run a statement as sa for test setup. Returns a function (port, sql, database=...)."""
    opened = []

    def run(port, sql, database="master", fetch=False):
        c = admin(port, database)
        opened.append(c)
        cur = c.cursor()
        cur.execute(sql)
        return cur.fetchall() if fetch else None

    yield run
    for c in opened:
        c.close()
