"""Provider-owned typed release declarations; no permission authority."""

from typing import Literal

from mcp.types import Tool
from pydantic import Field

from dlubal_rfem_mcp.core.config import Digest, ExactModel


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
    status: Literal["pending-review/provider-candidate"]
    engineeringAcceptance: Literal["not-performed"]
    previewDigest: Digest
    providerBuildId: Digest
    sourceDigest: Digest
    sourceFiles: list[SourceFileDigestV1]
    buildIdentity: InputManifestIdentityV1
