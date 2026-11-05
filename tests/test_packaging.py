"""pyproject.toml, requirements.txt and the app must agree, because the hosted build, CI and a plain `pip install` each read a different one."""
import re
import tomllib
from pathlib import Path

import subsweep

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


def requirements(name: str) -> list[str]:
    return [ln.strip() for ln in (ROOT / name).read_text().splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "-r"))]


def test_pyproject_dependencies_match_requirements_txt():
    assert sorted(PYPROJECT["project"]["dependencies"]) == sorted(requirements("requirements.txt"))


def test_dev_requirements_are_runtime_plus_pytest():
    text = (ROOT / "requirements-dev.txt").read_text()
    assert "-r requirements.txt" in text
    assert sorted(PYPROJECT["project"]["optional-dependencies"]["dev"]) == sorted(requirements("requirements-dev.txt"))
    assert not any(r.lower().startswith("pytest") for r in requirements("requirements.txt")), "the hosted bundle must not carry the test runner"


def test_version_comes_from_the_package_and_is_semver():
    assert PYPROJECT["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "subsweep.__version__"}
    assert re.fullmatch(r"\d+\.\d+\.\d+", subsweep.__version__)


def test_the_console_script_points_at_a_real_function():
    module, func = PYPROJECT["project"]["scripts"]["subsweep"].split(":")
    assert module == "subsweep.__main__" and callable(getattr(__import__(module, fromlist=[func]), func))


def test_supported_python_matches_ci_and_the_vercel_runtime():
    assert PYPROJECT["project"]["requires-python"] == ">=3.11"
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert all(v in ci for v in ('"3.11"', '"3.12"', '"3.13"'))
    assert (ROOT / ".python-version").read_text().strip() in ("3.11", "3.12", "3.13")
