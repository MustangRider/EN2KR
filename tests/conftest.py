import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point %APPDATA% storage at a temp dir and hide any real v1 settings."""
    monkeypatch.setenv("EN2KR_HOME", str(tmp_path / "home"))
    from en2kr import paths
    monkeypatch.setattr(paths, "legacy_settings_candidates", lambda: [str(tmp_path / "legacy.json")])
    return tmp_path
