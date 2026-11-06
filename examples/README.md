# Examples

Everything here is **sample data made for this kit**.

- `rdl/`: 12 SSRS reports (RDL 2016) written for the AdventureWorks sample database. They are not
  copies of Microsoft's sample reports. (We did not confirm the redistribution terms of Microsoft's
  report samples, so we wrote our own.) They are shaped to cover each classification:
  | Report | Why it is there |
  |---|---|
  | Sales/SalesByTerritory, Sales/OrderDetails, Inventory/ProductCatalog | plain reports (move) |
  | Sales/TopProducts | uses a chart (move, interactive hint) |
  | Inventory/BillOfMaterials | calls the stored procedure `dbo.uspGetBillOfMaterials` |
  | Sales/CustomerOrders | has a subreport (review) |
  | HR/EmployeeDirectory | has a custom code block (review) |
  | Sales/MonthlyCommissions | has a data-driven subscription, noted in `rdl/subscriptions.csv` (review) |
  | Finance/QuarterlyTaxSummary | runs 3 times a year (review) |
  | Purchasing/VendorSpend | old-style `*=` join the parser cannot read (review) |
  | Inventory/LegacyInventory | never ran (retire) |
  | Sales/OldPriceList | last ran 470 days ago (retire) |
  `Inventory/ProductCatalog` also has a multi-value parameter so `validate` shows a skip.
- `usage.csv`: a **synthetic** execution log in `ExecutionLog3` format, 520 days long.
- `usage_60days.csv`: the same shape, but only 55 days long, to show that no report is called retire from a short log.
- `generate_examples.py` rebuilds all of the above. The log dates are relative to today, so the
  classifications stay the same whenever you run it.

The reports have not been opened in Report Builder or rendered by an SSRS server. They are valid XML in
the RDL 2016 namespace and parse with this kit.
