"""Optional real Chromium -> Console -> native IAM Control -> Worker journeys."""

import asyncio
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from ..iam.native_iam import native_clients
from ..infrastructure.round_two_lab import free_origin

pytestmark = pytest.mark.anyio


@pytest.fixture
def console_enabled(request):
    if not request.config.getoption("--live-model-console"):
        pytest.skip("Opt in with --live-model-console and --live-management; requires optional Playwright")
    # Import only after the explicit opt-in; offline checks need no browser installation.
    from playwright.async_api import async_playwright

    return async_playwright


@pytest.mark.parametrize("catalog", ["available", "unavailable", "empty"])
async def test_console_provider_model_edit_and_execution(console_enabled, model_lab, catalog):
    from playwright.async_api import expect

    journey, lab = model_lab, model_lab.lab
    case = await journey.model(
        **({"catalog_status": 503} if catalog == "unavailable" else {"empty_catalog": catalog == "empty"})
    )
    origin = free_origin()
    frontend = Path(__file__).resolve().parents[3] / "frontend/apps/a13n-console"
    assert (frontend / "node_modules/vite/bin/vite.js").exists(), "Run make frontend-sync first"
    async with native_clients(lab, public_origin=origin) as (native, _unused):
        with (lab.root / "console.log").open("ab") as log:
            process = await asyncio.create_subprocess_exec(
                "node",
                "node_modules/vite/bin/vite.js",
                "--port",
                str(urlsplit(origin).port),
                cwd=frontend,
                env={**lab.environment, "A13N_CONSOLE_SERVICE_URL": str(native.live.http.base_url)},
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        lab.processes.append(process)
        await lab.ready(process, origin)
        workspace = await native.live.request("GET", journey.base)
        async with console_enabled() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context()
            await context.add_cookies(
                [
                    {
                        "name": "a13n_session",
                        "value": native.live.http.headers["cookie"].split("=", 1)[1],
                        "url": origin,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                ]
            )
            page = await context.new_page()
            page.set_default_timeout(20000)
            try:
                await page.goto(origin + "/workspace/" + workspace["key"] + "/models")
                await page.get_by_role("button", name="Add model", exact=True).click()
                dialog = page.get_by_role("dialog")
                await dialog.get_by_role("button", name="Connect provider", exact=True).click()
                await dialog.get_by_label("Provider type", exact=True).click()
                await page.get_by_role("option", name="OpenAI", exact=True).click()
                provider_name = "Browser provider " + uuid4().hex[:8]
                await dialog.get_by_label("Name", exact=True).fill(provider_name)
                await dialog.locator('input[name="provider-api-key"]').fill("fixture-browser-key")
                await dialog.get_by_role("button", name=re.compile("Advanced settings")).click()
                await dialog.get_by_label("Base URL", exact=True).fill(case["provider"]["configuration"]["base_url"])
                async with page.expect_response(
                    lambda response: response.request.method == "POST" and response.url.endswith("/model-providers")
                ) as saved_provider:
                    await dialog.get_by_role("button", name="Connect provider", exact=True).click()
                provider_response = await saved_provider.value
                assert provider_response.status == 201
                provider = await provider_response.json()
                assert provider["credential_configured"] and "credential" not in provider
                if catalog == "available":
                    await dialog.get_by_role("combobox", name="Model", exact=True).click()
                    await page.get_by_role("option", name="manual-model", exact=True).click()
                else:
                    await expect(
                        dialog.get_by_text("Catalog unavailable. Enter a model ID to continue.")
                    ).to_be_visible()
                    await dialog.get_by_role("tab", name="Enter model ID").click()
                    await dialog.get_by_label("Upstream model", exact=True).fill("manual-model")
                model_name, model_key = "Browser model " + uuid4().hex[:8], "browser-" + uuid4().hex
                await dialog.get_by_label("Name", exact=True).fill(model_name)
                await dialog.get_by_label("Model key", exact=True).fill(model_key)
                await dialog.get_by_role("button", name=re.compile("Parameters")).click()
                await dialog.get_by_role("tab", name="JSON", exact=True).click()
                await dialog.get_by_label("Settings JSON", exact=True).fill('{"temperature":0.25}')
                async with page.expect_response(
                    lambda response: response.request.method == "POST" and response.url.endswith("/models")
                ) as saved_model:
                    await dialog.get_by_role("button", name="Add model", exact=True).click()
                model_response = await saved_model.value
                assert model_response.status == 201
                model = await model_response.json()
                assert model["settings"] == {"temperature": 0.25}
                await expect(dialog).not_to_be_visible()
                await page.get_by_role("row").filter(has_text=model_name).click()
                await dialog.get_by_role("button", name=re.compile("Connection")).click()
                await dialog.get_by_role("button", name="Check connection").click()
                await expect(dialog.get_by_role("status")).to_contain_text("Succeeded", ignore_case=True)
                await dialog.get_by_label("Name", exact=True).fill(model_name + " edited")
                await expect(dialog.get_by_role("button", name="Check connection")).to_be_disabled()
                await dialog.get_by_role("button", name="Save changes").click()
                await expect(dialog).not_to_be_visible()
                await page.reload()
                await page.get_by_role("row").filter(has_text=model_name + " edited").click()
                await expect(dialog.get_by_label("Name", exact=True)).to_have_value(model_name + " edited")
                # Browser-created resources must execute through a real Worker as well.
                case.update(model=model, provider=provider)
                assert (await journey.invoke(case))["output_text"] == case["answer"]
                assert journey.requests(case, inference=True)[-1]["body"]["temperature"] == 0.25
            except BaseException:
                (lab.root / "console-accessibility.txt").write_text(await page.locator("body").aria_snapshot())
                raise
            finally:
                await context.close()
                await browser.close()
        await lab.stop(process)
