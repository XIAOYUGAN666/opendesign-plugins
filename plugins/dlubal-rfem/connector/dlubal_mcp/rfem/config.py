"""RFEM-specific settings."""

import os

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RFEMConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RFEM_", extra="ignore")

    exe_path: str = r"C:\Program Files\Dlubal\RFEM 6\bin\RFEM6.exe"
    url: str = "127.0.0.1"
    api_port: int = 9000
    api_key_name: str = "default"
    api_key: str | None = None
    execution_state_file: str | None = None

    @model_validator(mode="after")
    def resolve_exe_path(self) -> "RFEMConfig":
        if not os.path.exists(self.exe_path):
            from dlubal_mcp.core.config import find_dlubal_exe

            found = find_dlubal_exe("RFEM 6", "RFEM6.exe", "RFEM6")
            if found:
                self.exe_path = found
        return self

    @model_validator(mode="after")
    def resolve_api_key(self) -> "RFEMConfig":
        if self.api_key:
            return self
        env_key = os.environ.get("RFEM_API_KEY") or os.environ.get("DLUBAL_API_KEY")
        if env_key:
            self.api_key = env_key
            return self
        from dlubal_mcp.core.config import load_api_key_from_ini

        self.api_key = load_api_key_from_ini(self.api_key_name)
        return self
