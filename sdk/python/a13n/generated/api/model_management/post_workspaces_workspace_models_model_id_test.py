from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx2 as httpx

from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.model_connection_test_result import ModelConnectionTestResult
from ...models.model_test_request import ModelTestRequest
from ...types import UNSET, Response, Unset


def build_request(
    workspace: str,
    model_id: str,
    *,
    body: ModelTestRequest | Unset | None = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/workspaces/{workspace}/models/{model_id}/test".format(
            workspace=quote(str(workspace), safe=""),
            model_id=quote(str(model_id), safe=""),
        ),
    }

    if isinstance(body, ModelTestRequest):
        _kwargs["json"] = body.to_dict()
    else:
        _kwargs["json"] = body

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ModelConnectionTestResult:
    if response.status_code == 200:
        response_200 = ModelConnectionTestResult.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    response_default = ErrorResponse.from_dict(response.json())

    return response_default


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | ModelConnectionTestResult]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    workspace: str,
    model_id: str,
    *,
    client: AuthenticatedClient,
    body: ModelTestRequest | Unset | None = UNSET,
) -> Response[ErrorResponse | ModelConnectionTestResult]:
    """Test Model

    Args:
        workspace (str):
        model_id (str):
        body (ModelTestRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ModelConnectionTestResult]
    """

    kwargs = build_request(
        workspace=workspace,
        model_id=model_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    workspace: str,
    model_id: str,
    *,
    client: AuthenticatedClient,
    body: ModelTestRequest | Unset | None = UNSET,
) -> ErrorResponse | ModelConnectionTestResult | None:
    """Test Model

    Args:
        workspace (str):
        model_id (str):
        body (ModelTestRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ModelConnectionTestResult
    """

    return sync_detailed(
        workspace=workspace,
        model_id=model_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    workspace: str,
    model_id: str,
    *,
    client: AuthenticatedClient,
    body: ModelTestRequest | Unset | None = UNSET,
) -> Response[ErrorResponse | ModelConnectionTestResult]:
    """Test Model

    Args:
        workspace (str):
        model_id (str):
        body (ModelTestRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ModelConnectionTestResult]
    """

    kwargs = build_request(
        workspace=workspace,
        model_id=model_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    workspace: str,
    model_id: str,
    *,
    client: AuthenticatedClient,
    body: ModelTestRequest | Unset | None = UNSET,
) -> ErrorResponse | ModelConnectionTestResult | None:
    """Test Model

    Args:
        workspace (str):
        model_id (str):
        body (ModelTestRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ModelConnectionTestResult
    """

    return (
        await asyncio_detailed(
            workspace=workspace,
            model_id=model_id,
            client=client,
            body=body,
        )
    ).parsed
