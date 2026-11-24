"""Connector-owned typed release declarations; no permission authority."""

from typing import Annotated, Literal

from mcp.types import Tool
from pydantic import BaseModel, ConfigDict, Field

Digest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]


class ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ToolPreviewV1(ExactModel):
    normalizationVersion: Literal[1]
    tools: list[Tool]


class SourceFileDigestV1(ExactModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class InputManifestIdentityV1(ExactModel):
    profile: Literal["input-manifest-v1"]
    files: dict[str, str]


class RiskReviewV1(ExactModel):
    schemaVersion: Literal[1]
    status: Literal["pending-review/connector-candidate"]
    engineeringAcceptance: Literal["not-performed"]
    previewDigest: Digest
    connectorBuildId: Digest
    sourceDigest: Digest
    sourceFiles: list[SourceFileDigestV1]
    buildIdentity: InputManifestIdentityV1
