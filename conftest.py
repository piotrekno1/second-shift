# Fixtures that tests in more than one module share. Each app keeps its own in
# tests/fixtures.py, and this list makes them available to every test.
pytest_plugins = [
    "secondshift.connections.tests.fixtures",
    "secondshift.jobs.tests.fixtures",
    "secondshift.repositories.tests.fixtures",
    "secondshift.runs.tests.fixtures",
]
