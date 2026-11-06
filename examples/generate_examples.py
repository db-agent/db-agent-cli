"""Generate the sample SSRS reports (RDL 2016) and the synthetic usage log for the demo.

    python examples/generate_examples.py            # writes examples/rdl/**.rdl, subscriptions.csv, usage*.csv

The reports are written for the AdventureWorks sample database. They are SAMPLE DATA made for this
kit. They are not copies of Microsoft's sample reports. The usage log is invented and dated
relative to today so the classifications stay the same whenever you run the demo.
"""

from __future__ import annotations

import csv
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).parent
NS = (
    'xmlns="http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition" '
    'xmlns:rd="http://schemas.microsoft.com/SQLServer/reporting/reportdesigner"'
)


def tablix(name: str, fields: list[str], dataset: str, top: str = "0in", expr: dict[str, str] | None = None) -> str:
    expr = expr or {}
    cols = "".join(f"<TablixColumn><Width>1.3in</Width></TablixColumn>" for _ in fields)
    head = "".join(
        f'<TablixCell><CellContents><Textbox Name="h_{name}_{i}"><CanGrow>true</CanGrow><KeepTogether>true</KeepTogether>'
        f"<Paragraphs><Paragraph><TextRuns><TextRun><Value>{escape(f)}</Value><Style><FontWeight>Bold</FontWeight></Style></TextRun></TextRuns><Style /></Paragraph></Paragraphs>"
        f"<Style /></Textbox></CellContents></TablixCell>"
        for i, f in enumerate(fields)
    )
    det = "".join(
        f'<TablixCell><CellContents><Textbox Name="d_{name}_{i}"><CanGrow>true</CanGrow><KeepTogether>true</KeepTogether>'
        f"<Paragraphs><Paragraph><TextRuns><TextRun><Value>{escape(expr.get(f, '=Fields!' + f + '.Value'))}</Value><Style /></TextRun></TextRuns><Style /></Paragraph></Paragraphs>"
        f"<Style /></Textbox></CellContents></TablixCell>"
        for i, f in enumerate(fields)
    )
    members = "".join("<TablixMember />" for _ in fields)
    return (
        f'<Tablix Name="{name}Table"><TablixBody><TablixColumns>{cols}</TablixColumns><TablixRows>'
        f"<TablixRow><Height>0.25in</Height><TablixCells>{head}</TablixCells></TablixRow>"
        f"<TablixRow><Height>0.25in</Height><TablixCells>{det}</TablixCells></TablixRow>"
        f"</TablixRows></TablixBody><TablixColumnHierarchy><TablixMembers>{members}</TablixMembers></TablixColumnHierarchy>"
        f"<TablixRowHierarchy><TablixMembers><TablixMember><KeepWithGroup>After</KeepWithGroup></TablixMember>"
        f'<TablixMember><Group Name="{name}_Details" /></TablixMember></TablixMembers></TablixRowHierarchy>'
        f"<DataSetName>{dataset}</DataSetName><Top>{top}</Top><Left>0in</Left><Height>0.5in</Height><Width>{1.3 * len(fields):.1f}in</Width></Tablix>"
    )


def chart(name: str, dataset: str, cat: str, val: str) -> str:
    return (
        f'<Chart Name="{name}Chart"><ChartCategoryHierarchy><ChartMembers><ChartMember><Group Name="{name}_cat"><GroupExpressions>'
        f"<GroupExpression>=Fields!{cat}.Value</GroupExpression></GroupExpressions></Group><Label>=Fields!{cat}.Value</Label></ChartMember></ChartMembers></ChartCategoryHierarchy>"
        f"<ChartSeriesHierarchy><ChartMembers><ChartMember><Label>{val}</Label></ChartMember></ChartMembers></ChartSeriesHierarchy>"
        f'<ChartData><ChartSeriesCollection><ChartSeries Name="{val}"><ChartDataPoints><ChartDataPoint><ChartDataPointValues>'
        f"<Y>=Sum(Fields!{val}.Value)</Y></ChartDataPointValues></ChartDataPoint></ChartDataPoints></ChartSeries></ChartSeriesCollection></ChartData>"
        f"<DataSetName>{dataset}</DataSetName><Top>0in</Top><Left>0in</Left><Height>3in</Height><Width>6in</Width></Chart>"
    )


def dataset(name: str, sql: str, fields: list[str], params: list[str] | None = None, proc: bool = False) -> str:
    qp = "".join(f'<QueryParameter Name="@{p}"><Value>=Parameters!{p}.Value</Value></QueryParameter>' for p in (params or []))
    qp = f"<QueryParameters>{qp}</QueryParameters>" if qp else ""
    flds = "".join(f'<Field Name="{f}"><DataField>{f}</DataField><rd:TypeName>System.String</rd:TypeName></Field>' for f in fields)
    ctype = "<CommandType>StoredProcedure</CommandType>" if proc else ""
    return (
        f'<DataSet Name="{name}"><Query><DataSourceName>AdventureWorks</DataSourceName>{ctype}'
        f"<CommandText>{escape(sql)}</CommandText>{qp}</Query><Fields>{flds}</Fields></DataSet>"
    )


def parameter(name: str, dtype: str, default: str, multi: bool = False) -> str:
    return (
        f'<ReportParameter Name="{name}"><DataType>{dtype}</DataType>'
        f"<DefaultValue><Values><Value>{escape(default)}</Value></Values></DefaultValue>"
        f"<Prompt>{name}</Prompt>{'<MultiValue>true</MultiValue>' if multi else ''}</ReportParameter>"
    )


def report(items: str, datasets: str, params: str = "", code: str = "", header: bool = True, extra_body: str = "") -> str:
    page = (
        "<Page>"
        + ('<PageHeader><Height>0.4in</Height><PrintOnFirstPage>true</PrintOnFirstPage><PrintOnLastPage>true</PrintOnLastPage></PageHeader>' if header else "")
        + "<PageHeight>11in</PageHeight><PageWidth>8.5in</PageWidth><Style /></Page>"
    )
    return (
        f'<?xml version="1.0" encoding="utf-8"?>\n<Report {NS}><AutoRefresh>0</AutoRefresh>'
        '<DataSources><DataSource Name="AdventureWorks"><ConnectionProperties><DataProvider>SQL</DataProvider>'
        "<ConnectString>Data Source=sql2017.example.local;Initial Catalog=AdventureWorks2017</ConnectString>"
        "<IntegratedSecurity>true</IntegratedSecurity></ConnectionProperties><rd:SecurityType>Integrated</rd:SecurityType></DataSource></DataSources>"
        f"<DataSets>{datasets}</DataSets>"
        f"<ReportSections><ReportSection><Body><ReportItems>{items}{extra_body}</ReportItems><Height>3.5in</Height></Body><Width>6.5in</Width>{page}</ReportSection></ReportSections>"
        + (f"<ReportParameters>{params}</ReportParameters>" if params else "")
        + (f"<Code>{escape(code)}</Code>" if code else "")
        + "</Report>\n"
    )


def write(rel: str, text: str) -> None:
    p = HERE / "rdl" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def build_reports() -> None:
    write("Sales/SalesByTerritory.rdl", report(
        tablix("SalesTable", ["Territory", "CountryRegionCode", "Total"], "Sales"),
        dataset("Sales",
                "SELECT t.Name AS Territory, t.CountryRegionCode, SUM(h.TotalDue) AS Total "
                "FROM Sales.SalesOrderHeader h JOIN Sales.SalesTerritory t ON t.TerritoryID = h.TerritoryID "
                "WHERE h.OrderDate >= @StartDate AND h.OrderDate < @EndDate "
                "GROUP BY t.Name, t.CountryRegionCode ORDER BY Total DESC",
                ["Territory", "CountryRegionCode", "Total"], ["StartDate", "EndDate"]),
        parameter("StartDate", "DateTime", "2012-01-01") + parameter("EndDate", "DateTime", "2015-01-01")))
    write("Sales/TopProducts.rdl", report(
        chart("RevenueChart", "Top", "Name", "Revenue"),
        dataset("Top",
                "SELECT TOP (@TopN) p.Name, SUM(d.LineTotal) AS Revenue FROM Sales.SalesOrderDetail d "
                "JOIN Production.Product p ON p.ProductID = d.ProductID GROUP BY p.Name ORDER BY Revenue DESC",
                ["Name", "Revenue"], ["TopN"]),
        parameter("TopN", "Integer", "10")))
    write("Sales/OrderDetails.rdl", report(
        tablix("Lines", ["Name", "OrderQty", "UnitPrice", "LineTotal"], "Lines"),
        dataset("Lines",
                "SELECT d.SalesOrderID, p.Name, d.OrderQty, d.UnitPrice, d.LineTotal FROM Sales.SalesOrderDetail d "
                "JOIN Production.Product p ON p.ProductID = d.ProductID WHERE d.SalesOrderID = @SalesOrderID",
                ["SalesOrderID", "Name", "OrderQty", "UnitPrice", "LineTotal"], ["SalesOrderID"]),
        parameter("SalesOrderID", "Integer", "43659")))
    sub = ('<Subreport Name="OrderLines"><ReportName>OrderDetails</ReportName><Parameters><Parameter Name="SalesOrderID">'
           '<Value>=Fields!SalesOrderID.Value</Value></Parameter></Parameters><Top>0.6in</Top><Left>0in</Left><Height>1in</Height><Width>6in</Width></Subreport>')
    write("Sales/CustomerOrders.rdl", report(
        tablix("Orders", ["CustomerID", "SalesOrderID", "OrderDate", "TotalDue"], "Orders"),
        dataset("Orders",
                "SELECT TOP (100) c.CustomerID, h.SalesOrderID, h.OrderDate, h.TotalDue FROM Sales.Customer c "
                "JOIN Sales.SalesOrderHeader h ON h.CustomerID = c.CustomerID WHERE c.TerritoryID = @TerritoryID ORDER BY h.OrderDate DESC",
                ["CustomerID", "SalesOrderID", "OrderDate", "TotalDue"], ["TerritoryID"]),
        parameter("TerritoryID", "Integer", "1"), extra_body=sub))
    write("HR/EmployeeDirectory.rdl", report(
        tablix("Staff", ["FirstName", "LastName", "JobTitle", "FullName"], "Staff",
               expr={"FullName": "=Code.FormatName(Fields!FirstName.Value, Fields!LastName.Value)"}),
        dataset("Staff",
                "SELECT p.FirstName, p.LastName, e.JobTitle, e.HireDate FROM HumanResources.Employee e "
                "JOIN Person.Person p ON p.BusinessEntityID = e.BusinessEntityID",
                ["FirstName", "LastName", "JobTitle", "HireDate"]),
        code=("Public Function FormatName(ByVal first As String, ByVal last As String) As String\n"
              "  Return UCase(last) & \", \" & first\nEnd Function\n")))
    write("Inventory/BillOfMaterials.rdl", report(
        tablix("Bom", ["ProductAssemblyID", "ComponentID", "ComponentDesc", "PerAssemblyQty"], "Bom"),
        dataset("Bom", "dbo.uspGetBillOfMaterials", ["ProductAssemblyID", "ComponentID", "ComponentDesc", "PerAssemblyQty"],
                ["StartProductID", "CheckDate"], proc=True),
        parameter("StartProductID", "Integer", "893") + parameter("CheckDate", "DateTime", "2013-06-01")))
    write("Sales/MonthlyCommissions.rdl", report(
        tablix("Comm", ["BusinessEntityID", "CommissionPct", "SalesYTD"], "Comm"),
        dataset("Comm", "SELECT sp.BusinessEntityID, sp.CommissionPct, sp.SalesYTD FROM Sales.SalesPerson sp",
                ["BusinessEntityID", "CommissionPct", "SalesYTD"])))
    write("Inventory/LegacyInventory.rdl", report(
        tablix("Inv", ["Name", "Shelf", "Quantity"], "Inv"),
        dataset("Inv", "SELECT * FROM Production.ProductInventory i JOIN Production.Location l ON l.LocationID = i.LocationID",
                ["Name", "Shelf", "Quantity"])))
    write("Sales/OldPriceList.rdl", report(
        tablix("Prices", ["Name", "ListPrice", "StartDate"], "Prices"),
        dataset("Prices", "SELECT p.Name, h.ListPrice, h.StartDate FROM Production.ProductListPriceHistory h "
                          "JOIN Production.Product p ON p.ProductID = h.ProductID", ["Name", "ListPrice", "StartDate"])))
    write("Finance/QuarterlyTaxSummary.rdl", report(
        tablix("Tax", ["TerritoryID", "Tax"], "Tax"),
        dataset("Tax", "SELECT TerritoryID, SUM(TaxAmt) AS Tax FROM Sales.SalesOrderHeader GROUP BY TerritoryID", ["TerritoryID", "Tax"])))
    write("Purchasing/VendorSpend.rdl", report(
        tablix("Spend", ["Name", "Spend"], "Spend"),
        dataset("Spend",
                "SELECT v.Name, SUM(po.SubTotal) AS Spend FROM Purchasing.Vendor v, Purchasing.PurchaseOrderHeader po "
                "WHERE v.BusinessEntityID *= po.VendorID GROUP BY v.Name",
                ["Name", "Spend"])))
    write("Inventory/ProductCatalog.rdl", report(
        tablix("Cat", ["Name", "ProductNumber", "ListPrice"], "Cat"),
        dataset("Cat", "SELECT p.Name, p.ProductNumber, p.ListPrice FROM Production.Product p "
                       "WHERE p.ProductSubcategoryID IN (@Subcategory)", ["Name", "ProductNumber", "ListPrice"], ["Subcategory"]),
        parameter("Subcategory", "Integer", "1", multi=True)))
    (HERE / "rdl" / "subscriptions.csv").write_text(
        "ItemPath,DeliveryExtension,DataDriven\n/Sales/MonthlyCommissions,Report Server FileShare,yes\n", encoding="utf-8")


def build_usage(short: bool = False, today: datetime | None = None) -> list[dict]:
    """Synthetic ExecutionLog3-style rows. SAMPLE DATA. short=True gives a 60-day log."""
    rnd = random.Random(7)
    today = (today or datetime.now()).replace(hour=9, minute=0, second=0, microsecond=0)
    span = 55 if short else 520
    rows: list[dict] = []

    def add(path: str, day: int, user: str, ok: bool = True, rtype: str = "Interactive") -> None:
        start = today - timedelta(days=day, hours=rnd.randint(0, 6))
        ms = rnd.randint(800, 9000)
        rows.append({
            "ItemPath": path, "UserName": user, "RequestType": rtype,
            "TimeStart": start.strftime("%Y-%m-%d %H:%M:%S"),
            "TimeEnd": (start + timedelta(milliseconds=ms)).strftime("%Y-%m-%d %H:%M:%S"),
            "Status": "rsSuccess" if ok else "rsProcessingAborted",
        })

    users = [f"CORP\\user{n:02d}" for n in range(1, 9)]
    last = 52 if short else 400
    for d in range(1, last):  # busy report
        if d % 2 == 0:
            add("/Sales/SalesByTerritory", d, rnd.choice(users))
    for d in range(1, last, 7):
        add("/Sales/TopProducts", d, rnd.choice(users[:4]))
    for d in range(1, min(last, 120)):
        add("/Sales/CustomerOrders", d, rnd.choice(users[:6]))
        add("/Sales/OrderDetails", d, rnd.choice(users[:6]))
    for d in range(3, last, 30):
        add("/HR/EmployeeDirectory", d, rnd.choice(users[:2]))
    for d in range(2, last, 9):
        add("/Inventory/BillOfMaterials", d, rnd.choice(users[2:5]))
    for d in range(5, last, 30):
        add("/Sales/MonthlyCommissions", d, "CORP\\svc_reports", rtype="Subscription")
    for d in range(1, min(last, 200), 4):  # many failures
        add("/Purchasing/VendorSpend", d, rnd.choice(users[:3]), ok=(d % 8 != 1))
    for d in (20, 140, 300) if not short else (20,):
        add("/Finance/QuarterlyTaxSummary", d, "CORP\\cfo")
    for d in range(2, min(last, 150), 5):
        add("/Inventory/ProductCatalog", d, rnd.choice(users))
    if not short:
        for d in range(470, 500, 5):  # last run 470 days ago: retire
            add("/Sales/OldPriceList", d, "CORP\\user01")
    # /Inventory/LegacyInventory has no rows at all: retire
    add("/Sales/SalesByTerritory", span, rnd.choice(users))  # pins the oldest log entry
    rows.sort(key=lambda r: r["TimeStart"])
    return rows


def write_usage(rows: list[dict], name: str) -> None:
    cols = ["ItemPath", "UserName", "RequestType", "TimeStart", "TimeEnd", "Status"]
    with open(HERE / name, "w", newline="", encoding="utf-8") as fh:
        fh.write("# SAMPLE DATA: synthetic execution log made for the db-agent SQL Server Kit demo. Not from a real server.\n") if False else None
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    build_reports()
    write_usage(build_usage(short=False), "usage.csv")
    write_usage(build_usage(short=True), "usage_60days.csv")
    print("wrote examples/rdl/**.rdl, examples/usage.csv, examples/usage_60days.csv", file=sys.stderr)
