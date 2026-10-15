"""RFEM configuration derives only from the validated Host envelope."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr

from dlubal_rfem_mcp.core.config import ExactModel, StartupEnvelope


class RFEMConfig(ExactModel):
    url: Literal["127.0.0.1"] = "127.0.0.1"
    api_port: Literal[9000] = 9000
    api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    execution_state_file: str = Field(default="", repr=False)

    @classmethod
    def from_envelope(cls, envelope: StartupEnvelope) -> "RFEMConfig":
        return cls(
            api_key=envelope.configuration.api_key
            if envelope.configuration
            else SecretStr(""),
            execution_state_file=str(
                Path(envelope.data.stateDirectory) / "executions.sqlite3"
            )
            if envelope.data
            else "",
        )
