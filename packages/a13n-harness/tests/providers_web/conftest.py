import pytest
from a13n_harness.providers.web.builtins import built_in_web_providers


@pytest.fixture
def providers():
    return {item.type: item for item in built_in_web_providers()}


@pytest.fixture
def policy():
    class Policy:
        async def authorize(self, url, *, purpose):
            assert url.startswith("https://example.com/")
            assert purpose == "scrape"

    return Policy()
