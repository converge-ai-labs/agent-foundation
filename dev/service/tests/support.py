"""Typed local Environment construction for unit-test settings."""

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from a13n_service.settings import Settings
from sqlalchemy.engine import make_url

from dev.service.environment import Environment
from dev.service.instance import Instance, Ports


def environment_for(settings: Settings, root: Path) -> Environment:
    assert settings.database.url is not None and settings.redis.url is not None
    identity = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    settings = settings.model_copy(
        update={
            "service": settings.service.model_copy(update={"instance_id": f"local-{identity}"}),
            "iam": settings.iam.model_copy(update={"session_cookie_name": f"a13n_session_{identity}"}),
            "objects": settings.objects.model_copy(update={"local_root": root / "var/dev/service/objects"}),
            "filesystem": settings.filesystem.model_copy(update={"root": root / "var/dev/service/files"}),
        }
    )
    ports = Ports(
        service=settings.service.port,
        console=urlsplit(settings.iam.public_origin).port or 5173,
        model=urlsplit(settings.connectivity.http_origins[-1]).port or 18080,
        postgres=make_url(settings.database.url.get_secret_value()).port or 15432,
        redis=urlsplit(settings.redis.url.get_secret_value()).port or 16379,
        mem0=18888,
    )
    return Environment(settings, Instance(identity, str(root.resolve()), ports), root.resolve())
