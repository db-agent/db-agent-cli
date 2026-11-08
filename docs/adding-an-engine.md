# Adding a database engine

Layout:

```
src/dbagent/
  cli.py                 root: db-agent <engine> <verb>, plus `engines` and `version`
  engines/
    __init__.py          ENGINES registry (name, status, module)
    sqlserver/           the only implemented engine; everything in it is SQL Server specific
```

Steps:

1. Create `src/dbagent/engines/<name>/` with a `cli.py` that exposes a Typer `app` (the verbs).
2. Put the driver import in one module (`db.py`) and send every statement through a read-only guard
   for that engine's SQL dialect. `tests/test_architecture.py` shows the checks the SQL Server engine
   must pass; copy them for the new engine.
3. Flip the entry in `engines/__init__.py` from `planned` to `available` and set `module`.
4. Add tests, then document only what was tested live.

Nothing is shared between engines yet. `outputs`, `report` and `risks` in `sqlserver` depend on SQL Server
end-of-support data and version numbers. When a second engine needs the same report or risk model,
move the common parts into `dbagent/core/` then, with two real users to design against.
