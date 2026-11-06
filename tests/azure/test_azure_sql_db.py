"""Live test against an Azure SQL Database (free offer is enough) with AdventureWorksLT.

Skipped unless DBAGENT_TEST_AZURE_SERVER (full host name), DBAGENT_TEST_AZURE_USER,
DBAGENT_TEST_AZURE_PASSWORD and DBAGENT_TEST_AZURE_DATABASE are set.
"""
import os

import pytest

from dbagent_sqlserver.db import ConnectionConfig
from dbagent_sqlserver.inventory import collect
from dbagent_sqlserver.risks import assess

HOST = os.environ.get("DBAGENT_TEST_AZURE_SERVER")
pytestmark = pytest.mark.skipif(not HOST, reason="set DBAGENT_TEST_AZURE_* to run")


@pytest.fixture(scope="module")
def inv():
    cfg = ConnectionConfig(server=HOST, user=os.environ["DBAGENT_TEST_AZURE_USER"],
                           password=os.environ["DBAGENT_TEST_AZURE_PASSWORD"],
                           database=os.environ["DBAGENT_TEST_AZURE_DATABASE"], encrypt=True,
                           login_timeout=90, query_timeout=120)
    return collect(cfg)


def test_engine_is_azure_sql_database(inv):
    assert inv["server"]["engine_edition"] == 5


def test_sizes_come_from_own_files_and_tables_are_read(inv):
    db = os.environ["DBAGENT_TEST_AZURE_DATABASE"]
    row = next(d for d in inv["databases"] if d["name"] == db)
    assert row["data_mb"] and row["data_mb"] > 0
    assert any(t["name"] == "Customer" and t["rows"] == 847 for t in inv["objects"][db]["tables"])


def test_unavailable_features_are_explained_not_fatal(inv):
    text = "\n".join(inv["warnings"])
    assert "Azure SQL Database has no SQL Server Agent" in text
    assert "expected on Azure SQL Database" in text


def test_no_platform_noise_risks(inv):
    assert not [r for r in assess(inv) if r.id == "runtime-deprecated-feature"]
