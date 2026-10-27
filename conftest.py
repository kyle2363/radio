"""Never collect operational maintenance scripts as automated tests."""
from pathlib import Path

TESTS = Path(__file__).parent / "backend" / "tests"


def pytest_ignore_collect(collection_path, config):
    path = Path(collection_path)
    if path.is_file() and path.name.startswith("test_") and path.suffix == ".py":
        return not path.is_relative_to(TESTS)
