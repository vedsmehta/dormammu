import os

import pytest

from settings import load_env

load_env()


@pytest.fixture
def require_env():
    def check(*names: str) -> None:
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            pytest.skip(f"set {', '.join(missing)} in test/.env")

    return check


@pytest.fixture(autouse=True)
def isolated_results(tmp_path, monkeypatch, request):
    if "live" not in request.keywords:
        monkeypatch.setenv("CALL_RESULTS_DIR", str(tmp_path / "calls"))
