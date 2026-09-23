import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for entry in (str(ROOT), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

warnings.filterwarnings("ignore", category=DeprecationWarning)

from vulcan.config import Config  # noqa: E402
from vulcan.main import create_app  # noqa: E402
from vulcan.services import Services  # noqa: E402
from fixtures import make_library  # noqa: E402


def make_config(tmp_path: Path, **overrides) -> Config:
    base = dict(data_dir=tmp_path / "data", watch=False, autostart=False, thumb_size=128, data_dir_configured=True)
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def library(tmp_path) -> Path:
    folder = tmp_path / "library"
    make_library(folder)
    return folder


@pytest.fixture
def files(tmp_path) -> dict:
    return make_library(tmp_path / "library")


@pytest.fixture
def services(tmp_path):
    svc = Services(make_config(tmp_path))
    svc.worker.start()
    yield svc
    svc.stop()


@pytest.fixture
def scanned(services, library):
    """Services with the fixture library scanned."""
    root = services.add_root("Pruebas", str(library), None, None, False)
    assert services.worker.wait_idle(120)
    return services, root


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    app = create_app(make_config(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        test_client.services = app.state.services
        yield test_client
