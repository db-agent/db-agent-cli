"""The built wheel must carry the data files (a .gitignore rule once dropped data/classification.yaml)."""
import subprocess
import sys
import zipfile

import pytest


@pytest.mark.skipif(not __import__("importlib").util.find_spec("hatchling"), reason="hatchling not installed")
def test_wheel_contains_package_data(tmp_path):
    subprocess.run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation", "-w", str(tmp_path), "-q"], check=True)
    names = zipfile.ZipFile(next(tmp_path.glob("*.whl"))).namelist()
    assert "dbagent_sqlserver/data/classification.yaml" in names
    assert "dbagent_sqlserver/templates/summary.html.j2" in names
