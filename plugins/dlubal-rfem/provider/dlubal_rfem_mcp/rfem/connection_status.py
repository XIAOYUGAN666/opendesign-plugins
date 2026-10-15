"""Typed safe result of the existing fixed read-only application-info tool."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from dlubal_rfem_mcp.core.config import ExactModel

ConnectionFailureCategory = Literal[
    "authenticationFailed",
    "authorizationRestricted",
    "quotaRestricted",
    "apiAccessRestricted",
    "softwareUnavailable",
    "unsupportedSoftwareVersion",
    "resourceBusy",
]


class ApplicationInfoSuccessV1(ExactModel):
    status: Literal["ok"]
    version: str = Field(min_length=1, max_length=40)


class ApplicationInfoFailureV1(ExactModel):
    status: Literal["error"]
    failureCategory: ConnectionFailureCategory


ApplicationInfoResponseV1 = Annotated[
    ApplicationInfoSuccessV1 | ApplicationInfoFailureV1, Field(discriminator="status")
]
application_info_response: TypeAdapter[ApplicationInfoResponseV1] = TypeAdapter(
    ApplicationInfoResponseV1
)


def application_info_schema():
    return {"type": "object", **application_info_response.json_schema()}
