# make demo-files   no database needed: reads the sample reports and the synthetic usage log
# make demo         full demo: needs the containers (see README), runs all four commands
PY ?= .venv/bin/python
CLI = $(PY) -m dbagent_sqlserver.cli
OUT ?= dbagent-sqlserver-output
export DBAGENT_SQL_PASSWORD ?= DbAgent_Demo_2017!
export DBAGENT_SOURCE_PASSWORD ?= DbAgent_Demo_2017!
export DBAGENT_TARGET_PASSWORD ?= DbAgent_Demo_2017!
DB = AdventureWorks2017

test:
	$(PY) -m pytest -q --ignore=tests/integration

test-live:
	DBAGENT_TEST_SERVER=localhost $(PY) -m pytest -q

examples:
	$(PY) examples/generate_examples.py

demo-files: examples
	$(CLI) ssrs --rdl-folder examples/rdl --usage-csv examples/usage.csv --out $(OUT)
	open $(OUT)/summary.html

demo: examples
	$(CLI) inventory --server localhost --port 14331 --user sa --no-encrypt --databases $(DB) --out $(OUT)
	$(CLI) ssrs --rdl-folder examples/rdl --usage-csv examples/usage.csv --data-database $(DB) --server localhost --port 14331 --user sa --no-encrypt --out $(OUT)
	$(CLI) validate --source "server=localhost,port=14331,database=$(DB),user=sa,encrypt=no" --target "server=localhost,port=14332,database=$(DB),user=sa,encrypt=no" --reports $(OUT)/reports.csv --allow-exec --out $(OUT)
	$(CLI) report --out $(OUT)
	open $(OUT)/summary.html

.PHONY: test test-live examples demo-files demo
