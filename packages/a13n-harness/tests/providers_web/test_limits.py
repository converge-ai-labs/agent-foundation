"""Reusable limits apply equally to custom callbacks and built-in Web operations."""

from datetime import UTC, datetime

import pytest
from a13n_harness.providers.authentication import Authentication, CredentialMode
from a13n_harness.providers.usage import ProviderUsage
from a13n_harness.providers.web import ScrapeOptions, WebProviderDefinition, WebScrapeRequest, WebScrapeResult
from anyio import CancelScope, current_time, fail_after, move_on_after, sleep, sleep_forever
from pydantic import BaseModel, ConfigDict


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def definition(scrape):
    return WebProviderDefinition(
        type="custom",
        display_name="Custom",
        configuration_model=Empty,
        credential_model=Empty,
        setup_url="https://example.com/setup",
        authentication=Authentication(mode=CredentialMode.forbidden),
        scrape=scrape,
    )


def request(*, budget=4, deadline=1):
    return WebScrapeRequest(
        url="https://example.com/article",
        max_content_bytes=budget,
        deadline_seconds=deadline,
        max_redirects=0,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "request_budget,provider_budget,content,already_truncated,expected,truncated",
    [
        (4, 5, "界" * 10, False, "界", True),
        (5, 4, "界" * 10, False, "界", True),
        (2, 5, "界", False, "", True),
        (6, 6, "界界", False, "界界", False),
        (6, 6, "界界", True, "界界", True),
        (6, 6, "", True, "", True),
    ],
)
async def test_custom_scrape_enforces_utf8_limits_without_losing_metadata(
    policy, request_budget, provider_budget, content, already_truncated, expected, truncated
):
    original = WebScrapeResult(
        content=content,
        source_url="https://example.com/article",
        canonical_url="https://example.com/canonical",
        title="Title",
        truncated=already_truncated,
        usage=(
            ProviderUsage(
                usage_id="usage_test",
                provider="custom",
                product="scrape",
                timestamp=datetime(2026, 9, 18, tzinfo=UTC),
                cost="0.01",
                currency="USD",
            ),
        ),
    )

    async def scrape(configuration, credential, requested, options, transport, callback_policy):
        return original

    async with definition(scrape).open({}, None, options=ScrapeOptions(max_content_bytes=provider_budget)) as web:
        result = await web.scrape(request(budget=request_budget), policy=policy)
    assert result.content == expected
    assert len(result.content.encode("utf-8")) <= min(request_budget, provider_budget)
    assert result.truncated is truncated
    assert result.model_dump(exclude={"content", "truncated"}) == original.model_dump(exclude={"content", "truncated"})
    assert original.content == content and original.truncated is already_truncated


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["policy", "callback"])
async def test_deadline_includes_policy_and_callback_and_allows_cleanup(boundary):
    steps = []

    class Policy:
        async def authorize(self, url, *, purpose):
            steps.append("policy")
            if boundary == "policy":
                try:
                    await sleep_forever()
                finally:
                    steps.append("policy cleanup")

    async def scrape(configuration, credential, requested, options, transport, callback_policy):
        steps.append("callback")
        try:
            await sleep_forever()
        finally:
            with CancelScope(shield=True):
                await sleep(0.001)
                steps.append("callback cleanup")

    started = current_time()
    with pytest.raises(TimeoutError), fail_after(2):
        async with definition(scrape).open({}, None) as web:
            await web.scrape(request(deadline=0.01), policy=Policy())
    assert current_time() - started < 1
    assert steps == (
        ["policy", "policy cleanup"] if boundary == "policy" else ["policy", "callback", "callback cleanup"]
    )


@pytest.mark.anyio
async def test_outer_cancellation_is_not_converted_to_timeout_or_provider_failure(policy):
    cleaned = False

    async def scrape(configuration, credential, requested, options, transport, callback_policy):
        nonlocal cleaned
        try:
            await sleep_forever()
        finally:
            cleaned = True

    with move_on_after(0.01) as scope:
        async with definition(scrape).open({}, None) as web:
            await web.scrape(request(deadline=1), policy=policy)
        pytest.fail("cancellation was swallowed")
    assert scope.cancelled_caught and cleaned
