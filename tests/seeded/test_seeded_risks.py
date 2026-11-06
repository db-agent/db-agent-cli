"""Run `scripts/seed_risky_objects.py` first, then set DBAGENT_SEED_SERVER/PORT/USER/PASSWORD (the same variables).
Checks that the inventory finds each seeded legacy object as the expected risk on a real engine."""
import os

import pytest

from dbagent_sqlserver.db import ConnectionConfig
from dbagent_sqlserver.inventory import collect
from dbagent_sqlserver.risks import assess

pytestmark = pytest.mark.skipif(not os.environ.get("DBAGENT_SEED_SERVER"), reason="set DBAGENT_SEED_* to run")

EXPECTED_ON_RISKLAB = {
    "dep-text-ntext-image", "dep-create-default-rule", "dep-set-rowcount", "dep-group-by-all", "dep-weak-crypto",
    "dep-table-hint-without-with", "dep-compat-views", "dep-dbcc", "dep-set-fmtonly", "xp-cmdshell",
    "cross-db-ref", "encrypted-module", "synonym-cross-db", "service-broker", "dep-set-ansi-off", "synonym-linked-server",
}


@pytest.fixture(scope="module")
def risks():
    cfg = ConnectionConfig(server=os.environ["DBAGENT_SEED_SERVER"], port=int(os.environ.get("DBAGENT_SEED_PORT", 1433)),
                           user=os.environ["DBAGENT_SEED_USER"], password=os.environ["DBAGENT_SEED_PASSWORD"],
                           encrypt=os.environ.get("DBAGENT_SEED_ENCRYPT") == "1", login_timeout=60, query_timeout=300)
    inv = collect(cfg)
    return inv, assess(inv)


def test_each_seeded_object_is_found(risks):
    _, rs = risks
    found = {r.id for r in rs if r.database == "RiskLab"}
    assert not (EXPECTED_ON_RISKLAB - found), f"missed: {sorted(EXPECTED_ON_RISKLAB - found)}"


@pytest.mark.skipif(not os.environ.get("DBAGENT_SEED_CLR"), reason="seeded without --clr-hex")
def test_clr_assembly_found(risks):
    _, rs = risks
    assert any(r.id == "clr" and r.database == "RiskLab" for r in rs)


def test_linked_server_and_agent_job_found(risks):
    inv, rs = risks
    ids = {r.id for r in rs}
    assert "linked-server" in ids
    if any(str(ls.get("provider", "")).upper() == "SQLOLEDB" for ls in inv["linked_servers"]):  # some engines map it to SQLNCLI
        assert "dep-sqloledb" in ids
    assert any(j["job_name"] == "RiskLab nightly" for j in inv["agent_jobs"])
    assert any(r.object.startswith("RiskLab nightly") for r in rs if r.id in ("dep-set-rowcount", "dep-compat-views", "dep-weak-crypto"))
