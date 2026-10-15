"""Exact Host-owned startup contract; no environment/config discovery."""

from pathlib import PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
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


class SoftwareSettings(ExactModel):
    safeIdentity: str = Field(pattern=r"^si_[a-zA-Z0-9_-]{1,128}$")
    build: Literal["6.14.0013.249"]


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


class StartupEnvelopeV2(ExactModel):
    schemaVersion: Literal[2]
    mode: Literal["metadata", "execution"]
    connectorInstanceId: str = Field(pattern=r"^ci_[a-f0-9]{32}$")
    generation: PositiveRevision
    bind: BindSettings
    bearerToken: SecretStr = Field(repr=False)
    configuration: ConfigurationV1 | None = Field(default=None, repr=False)
    data: DataSettings | None = None
    software: SoftwareSettings | None = None
    providerBuildId: Digest

    @field_validator("schemaVersion", mode="before")
    @classmethod
    def integer_schema(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("invalidSchemaVersion")
        return value

    @field_validator("bearerToken")
    @classmethod
    def bounded_token(cls, value: SecretStr) -> SecretStr:
        plain = value.get_secret_value()
        if (
            not 32 <= len(plain) <= 512
            or not plain.isascii()
            or not all(char.isalnum() or char in "_-" for char in plain)
        ):
            raise ValueError("invalidBearer")
        return value

    @model_validator(mode="after")
    def separate_credentials(self) -> Self:
        if self.mode == "execution" and any(
            value is None for value in (self.configuration, self.data, self.software)
        ):
            raise ValueError("engineeringPreconditionMissing")
        if self.mode == "metadata" and any(
            value is not None
            for value in (self.configuration, self.data, self.software)
        ):
            raise ValueError("metadataMustNotCarryEngineeringState")
        if (
            self.configuration is not None
            and self.bearerToken.get_secret_value()
            == self.configuration.api_key.get_secret_value()
        ):
            raise ValueError("credentialsMustDiffer")
        return self


StartupEnvelope = StartupEnvelopeV2


class ProviderReadyV2(ExactModel):
    schemaVersion: Literal[2] = 2
    kind: Literal["ready"] = "ready"
    mode: Literal["metadata", "execution"]
    connectorInstanceId: str = Field(pattern=r"^ci_[a-f0-9]{32}$")
    generation: PositiveRevision
    providerBuildId: Digest
    port: Annotated[int, Field(strict=True, ge=1024, le=65535)]


class ProviderLifecycleStatusV1(ExactModel):
    schemaVersion: Literal[1] = 1
    kind: Literal["status"] = "status"
    connectorInstanceId: str = Field(pattern=r"^ci_[a-f0-9]{32}$")
    generation: PositiveRevision
    sequence: PositiveRevision
    state: Literal["idle", "running", "unknown", "draining", "unverifiable"]
    inFlight: Annotated[int, Field(strict=True, ge=0)]
