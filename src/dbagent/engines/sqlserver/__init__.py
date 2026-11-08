"""db-agent SQL Server Kit: read-only inventory of SQL Server and SSRS reports."""

from dbagent import __version__  # noqa: F401  (one version for the whole CLI)

SUPPORTED_SQL_SERVER = "SQL Server 2016 to 2022 (read), Azure SQL Database and Managed Instance (read)"

# Verified against Microsoft's lifecycle pages on 2026-11-05.
END_OF_SUPPORT = {
    13: ("SQL Server 2016", "2026-07-14"),
    14: ("SQL Server 2017", "2027-10-12"),
}
