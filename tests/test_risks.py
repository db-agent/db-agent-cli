from dbagent.engines.sqlserver import risks as R
from dbagent.engines.sqlserver.rules import scan_code


def ids(rs, **kw):
    return [r for r in rs if all(getattr(r, k) == v for k, v in kw.items())]


def test_code_rules_find_real_use_and_ignore_comments_and_strings():
    hits = {r.id: (line, text) for r, line, text in scan_code(
        "SELECT 1;\nSET ROWCOUNT 10;\n-- SET ROWCOUNT 5\nSELECT 'sp_addrole';\nEXEC sp_addlogin 'a';\nSELECT * FROM t WITH (NOLOCK);\nSELECT * FROM t (NOLOCK);")}
    assert hits["dep-set-rowcount"][0] == 2
    assert hits["dep-sp-removed-security-and-admin"][0] == 5
    assert hits["dep-table-hint-without-with"][0] == 7   # not line 6
    assert len([1 for r, _, _ in scan_code("SELECT 'SET ROWCOUNT 5' -- SET ROWCOUNT 6") if r.id == "dep-set-rowcount"]) == 0


def test_assess_covers_the_main_findings(fake_inventory):
    rs = R.assess(fake_inventory)
    found = {r.id for r in rs}
    for expected in ["compat-deprecated", "compat-below-target", "replication", "linked-server", "dep-sqloledb", "mirroring",
                     "runtime-deprecated-feature", "dep-text-ntext-image", "stretch", "clr", "service-broker", "fulltext",
                     "cross-db-ref", "cross-server-ref", "synonym-linked-server", "synonym-cross-db", "dep-set-rowcount",
                     "dep-sp-removed-security-and-admin", "dep-compat-views", "dep-weak-crypto", "dep-table-hint-without-with",
                     "dep-set-ansi-off", "encrypted-module", "xp-cmdshell", "end-of-support"]:
        assert expected in found, expected
    assert all(r.doc.startswith("https://learn.microsoft.com") for r in rs)           # every risk cites Microsoft
    assert rs[0].severity == "high" and [r.severity for r in rs] == sorted([r.severity for r in rs], key=R.SEVERITY_ORDER.get)
    agent = ids(rs, database="Sales", object="Nightly step 1")
    assert any(r.id == "dep-set-rowcount" for r in agent)
    assert not ids(rs, database="master")                                              # system databases are skipped


def test_compat_level_checks_follow_the_target(fake_inventory):
    fake_inventory["databases"][2]["compatibility_level"] = 150
    r22 = [r for r in R.assess(fake_inventory, 16) if r.database == "Old"]
    r19 = [r for r in R.assess(fake_inventory, 15) if r.database == "Old"]
    assert r22 and not r19


def test_strip_secrets_removes_agent_commands_and_linked_server_data_source(fake_inventory):
    safe = R.strip_secrets(fake_inventory)
    assert "hunter2" not in repr(safe)


def test_false_positives_seen_on_a_live_server_are_not_reported(fake_inventory):
    rs = R.assess(fake_inventory)
    assert not [r for r in rs if r.id == "cross-db-ref" and "Resume" in r.detail]          # XML method call, not a database
    assert [r for r in rs if r.id == "cross-db-ref" and "Other" in r.detail]
    counters = [r.object for r in rs if r.id == "runtime-deprecated-feature"]
    assert "Database compatibility level 140" not in counters and "Database compatibility level 100" in counters
