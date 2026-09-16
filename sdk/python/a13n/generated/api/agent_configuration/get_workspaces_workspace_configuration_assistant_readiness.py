from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.assistant_readiness import AssistantReadiness
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    *,
    target_agent_id: str | Unset | None = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_target_agent_id: str | Unset | None
    if isinstance(target_agent_id, Unset):
        json_target_agent_id = UNSET
    else:
        json_target_agent_id = target_agent_id
    params["target_agent_id"] = json_target_agent_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/workspaces/{workspace}/configuration-assistant/readiness".format(
            workspace=quote(str(workspace), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AssistantReadiness | ErrorResponse:
    if response.status_code == 200:
        response_200 = AssistantReadiness.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[AssistantReadiness | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    target_agent_id: str | Unset | None = UNSET,
) -> Response[AssistantReadiness | ErrorResponse]:
    """Readiness

    Args:
        workspace (str):
        target_agent_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AssistantReadiness | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        target_agent_id=target_agent_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    *,
    client: AuthenticatedClient,
    target_agent_id: str | Unset | None = UNSET,
) -> AssistantReadiness | ErrorResponse | None:
    """Readiness

    Args:
        workspace (str):
        target_agent_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AssistantReadiness | ErrorResponse
    """

    return sync_detailed(
        workspace=workspace,
        client=client,
        target_agent_id=target_agent_id,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    *,
    client: AuthenticatedClient,
    target_agent_id: str | Unset | None = UNSET,
) -> Response[AssistantReadiness | ErrorResponse]:
    """Readiness

    Args:
        workspace (str):
        target_agent_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AssistantReadiness | ErrorResponse]
    """

    kwargs = build_request(
        workspace=workspace,
        target_agent_id=target_agent_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    *,
    client: AuthenticatedClient,
    target_agent_id: str | Unset | None = UNSET,
) -> AssistantReadiness | ErrorResponse | None:
    """Readiness

    Args:
        workspace (str):
        target_agent_id (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AssistantReadiness | ErrorResponse
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            client=client,
            target_agent_id=target_agent_id,
        )
    ).parsed
