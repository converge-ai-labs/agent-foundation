"""Cloud recipes must install the guest tested with the current Service protocol."""

import hashlib
import re
from pathlib import Path

import pytest
from a13n_service.providers.environment import desktop_guest
from a13n_service.providers.environments.daytona import desktop_build as daytona
from a13n_service.providers.environments.e2b import desktop_build as e2b
from a13n_service.providers.environments.modal import desktop_build as modal
from a13n_service.providers.environments.sprites import desktop_install as sprites
from a13n_service.providers.environments.vercel import desktop_build as vercel

pytestmark = pytest.mark.parametrize(
    ("builder", "render"),
    [
        pytest.param(daytona, daytona.specification, id="daytona"),
        pytest.param(e2b, e2b.specification, id="e2b"),
        pytest.param(modal, modal.specification, id="modal"),
        pytest.param(sprites, sprites.installation_script, id="sprites"),
        pytest.param(vercel, vercel.specification, id="vercel"),
    ],
)

GUEST_SOURCE = "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py"


def test_recipe_installs_current_guest(builder, render):
    digest = hashlib.sha256(Path(desktop_guest.__file__).read_bytes()).hexdigest()
    assert [(destination, checksum) for source, destination, checksum in builder.FILES if source == GUEST_SOURCE] == [
        ("/opt/a13n-desktop-access.py", digest)
    ]
    assert re.fullmatch(r"[0-9a-f]{40}", builder.SOURCE_REVISION)
    manifest = str(render())
    assert (
        f"https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/{builder.SOURCE_REVISION}/{GUEST_SOURCE}"
        in manifest
    )
    assert f"{digest}  /opt/a13n-desktop-access.py" in manifest


@pytest.mark.parametrize("changed", ["source", "checksum"])
def test_guest_update_changes_recipe_revision(builder, render, monkeypatch, changed):
    original = builder.revision()
    if changed == "source":
        monkeypatch.setattr(builder, "SOURCE_REVISION", "0" * 40)
    else:
        monkeypatch.setattr(
            builder,
            "FILES",
            tuple(
                (source, destination, "0" * 64 if source == GUEST_SOURCE else checksum)
                for source, destination, checksum in builder.FILES
            ),
        )
    assert builder.revision() != original
