# Contributing

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make test          # no database needed
```

## Release

1. Bump the version in **both** `pyproject.toml` and `src/db_agent/__init__.py`, and date the entry in `CHANGELOG.md`.
2. Merge to `main`, then tag `vX.Y.Z` on `main`. The `release` workflow builds, runs `twine check --strict`, checks the tag
   equals both version strings, installs the wheel in a clean venv, and publishes to PyPI with Trusted Publishing (no tokens).
   Use the manual `testpypi` run first for a dry run.
3. One time: on pypi.org add a (pending) publisher for project `db-agent-kit`, repo `db-agent/db-agent-cli`, workflow `release.yml`, environment `pypi`
   (and the same on test.pypi.org with environment `testpypi`). Create the matching environments in the GitHub repo settings.

Never publish by hand.
