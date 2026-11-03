"""Read-only viewing is independent of the input-control lease."""

from datetime import timedelta
from urllib.parse import quote

from sqlalchemy import func, select

from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.environment import desktop_access as wire
from a13n_service.runs.environments.desktop import access, control, identity
from a13n_service.runs.environments.desktop.schemas import OpenViewer, Viewer, viewer_url
from a13n_service.runs.environments.desktop.tables import DesktopViewerRow
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import Principal


async def open_viewer(
    runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str, body: OpenViewer
) -> Viewer:
    async with transaction(runtime.storage) as session:
        environment, row, selected = await control.find(session, runtime, actor, workspace_id, environment_id)
        current = await now(session)
        if body.mode == "control":
            if (body.generation, body.episode) != (row.generation, row.episode):
                raise conflict("environment", environment_id, "desktop_control_changed")
            owner = await session.get(EnvironmentRow, row.environment_id) if row.environment_id else None
            if (
                row.control != "human"
                or row.controller_id != actor.id
                or row.expires_at is None
                or row.expires_at <= current
                or owner is None
                or not await control.controller_allowed(session, runtime, owner, row)
            ):
                raise conflict("environment", environment_id, "desktop_viewer_unavailable")
            viewer = control.reveal(runtime, owner, row)
            if viewer is None or viewer.expires_at <= current:
                raise conflict("environment", environment_id, "desktop_viewer_expired")
            viewer.expires_at = min(viewer.expires_at, row.expires_at)
            return viewer
        # Reserve before remote dispatch, so an uncertain call also prevents idle reclamation.
        deadline = current + timedelta(seconds=60)
        key, generation = row.target_key, row.generation
        if body.viewer_id is not None:
            reader = await lock(session, DesktopViewerRow, body.viewer_id)
            if (
                reader is None
                or reader.control_id != row.id
                or reader.environment_id != environment.id
                or reader.principal_id != actor.id
                or reader.expires_at <= current
                or reader.guest_generation is None
            ):
                raise conflict("environment", environment_id, "desktop_viewer_unavailable")
            reader.expires_at = max(reader.expires_at, deadline)
        else:
            readers = await session.scalar(
                select(func.count())
                .select_from(DesktopViewerRow)
                .where(DesktopViewerRow.control_id == row.id, DesktopViewerRow.expires_at > current)
            )
            if (readers or 0) >= 16:
                raise conflict("environment", environment_id, "desktop_viewer_limit")
            reader = DesktopViewerRow(
                id=new_object_id("dview"),
                control_id=row.id,
                environment_id=environment.id,
                principal_id=actor.id,
                episode=max(1, row.episode),
                expires_at=deadline,
            )
            session.add(reader)
        viewer_id, episode, guest_generation = reader.id, reader.episode, reader.guest_generation
    async with access.connection(runtime, selected, new_object_id("dopen")) as send:
        description = await send(wire.Describe())
        if not isinstance(description, wire.Description):
            raise conflict("environment", environment_id, "desktop_endpoint_unavailable")
        if guest_generation is not None and description.generation != guest_generation:
            raise conflict("environment", environment_id, "desktop_target_changed")
        route = viewer_url(description.websocket_url)
        granted = await send(
            wire.Grant(
                id=viewer_id,
                generation=description.generation,
                epoch=episode,
                mode="view",
                expires_at=deadline.timestamp(),
            )
        )
        if (
            not isinstance(granted, wire.AccessGrant)
            or granted.state != "active"
            or granted.token is None
            or granted.password is None
        ):
            raise conflict("environment", environment_id, "desktop_viewer_unavailable")
        async with transaction(runtime.storage) as session:
            environment, row, _ = await control.find(session, runtime, actor, workspace_id, environment_id)
            if (
                identity.environment_key(environment) != key
                or row.generation != generation
                or deadline <= await now(session)
            ):
                raise conflict("environment", environment_id, "desktop_target_changed")
            reader = await lock(session, DesktopViewerRow, viewer_id)
            assert reader is not None
            reader.guest_generation = description.generation
        return Viewer(
            viewer_id=viewer_id,
            mode="view",
            websocket_url=route + "?token=" + quote(granted.token.get_secret_value(), safe=""),
            password=granted.password.get_secret_value(),
            expires_at=deadline,
            generation=generation,
            episode=row.episode,
        )
