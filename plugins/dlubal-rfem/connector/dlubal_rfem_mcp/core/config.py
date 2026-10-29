"""Exact Host-owned startup contract; no environment/config discovery."""

from pathlib import PureWindowsPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
)

PositiveRevision = Annotated[int, Field(strict=True, ge=1, le=2**53 - 1)]
Digest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]


class ExactModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, strict=True
    )


class BindSettings(ExactModel):
    host: Literal["127.0.0.1"]
    port: Annotated[int, Field(strict=True, ge=1024, le=65535)]
    path: Literal["/mcp"]


class Credential(ExactModel):
    fieldId: Literal["dlubal-api-key"]
    revision: PositiveRevision
    value: SecretStr = Field(repr=False)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: SecretStr) -> SecretStr:
        plain = value.get_secret_value()
        if not 1 <= len(plain) <= 4096 or any(ord(char) < 32 for char in plain):
            raise ValueError("credentialMissing")
        return value


class DataSettings(ExactModel):
    stateDirectory: str = Field(repr=False, min_length=1, max_length=32767)

    @field_validator("stateDirectory")
    @classmethod
    def canonical_local_file(cls, value: str) -> str:
        path = PureWindowsPath(value)
        if (
            not path.is_absolute()
            or len(path.drive) != 2
            or path.drive[1] != ":"
            or ".." in path.parts
            or any(ord(char) < 32 for char in value)
            or ":" in value[2:]
            or path.name in ("", ".")
        ):
            raise ValueError("invalidDataPath")
        return value


class ConfigurationV1(ExactModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
        title="Dlubal RFEM",
        json_schema_extra={
            "description": "RFEM 插件已安装。使用建模、计算和结果读取功能前，需要配置 Dlubal API Key。"
        },
    )
    api_key: SecretStr = Field(
        alias="dlubal-api-key",
        min_length=1,
        max_length=4096,
        repr=False,
        title="Dlubal API Key",
        description="[Dlubal 官方 API Key 获取指南](https://apidocs.dlubal.com/access_control.html)",
    )

    @field_validator("api_key")
    @classmethod
    def bounded_key(cls, value: SecretStr) -> SecretStr:
        return Credential.validate_value(value)


class ConnectorExecutionStatusV1(ExactModel):
    schemaVersion: Literal[1] = 1
    kind: Literal["status"] = "status"
    connectorInstanceId: str = Field(pattern=r"^ci_[a-f0-9]{32}$")
    generation: PositiveRevision
    sequence: PositiveRevision
    state: Literal["idle", "running", "unknown", "draining", "unverifiable"]
    inFlight: Annotated[int, Field(strict=True, ge=0)]
