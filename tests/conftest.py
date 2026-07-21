import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timetable.sample_data import generate_sample_instance, load_reference_instance


@pytest.fixture(scope="session")
def small_problem():
    return generate_sample_instance("small", seed=42)


@pytest.fixture(scope="session")
def medium_problem():
    return generate_sample_instance("medium", seed=42)


@pytest.fixture(scope="session")
def reference_problem():
    return load_reference_instance()
