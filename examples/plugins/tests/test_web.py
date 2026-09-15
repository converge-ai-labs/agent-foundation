from __future__ import annotations

import asyncio

from a13n_plugin_examples.demo_web import run_web_demo


def test_middleware_observes_feature_with_host_owned_run_bindings() -> None:
    assert asyncio.run(run_web_demo()) == "Host-owned offline Web response"
