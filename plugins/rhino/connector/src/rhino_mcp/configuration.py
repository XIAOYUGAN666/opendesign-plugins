"""Package-owned ordinary preferences read from the host's plugin data directory."""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConfigurationV1(BaseModel):
    model_config = ConfigDict(title="Rhino", strict=True, extra="forbid", frozen=True)

    targetPort: int = Field(0, ge=0, le=65535, title="目标端口", description="0 为自动选择")
    timeout: float = Field(15.0, ge=0.1, le=120, title="响应超时（秒）")
    validation: Literal["off", "warn", "strict"] = Field("warn", title="工具参数和结果校验")
    logLevel: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field("INFO", title="日志级别")
    perception: bool = Field(False, title="记录对象变化和几何检查")

    @classmethod
    def read(cls) -> "ConfigurationV1":
        directory = os.environ.get("PLUGIN_DATA")
        if not directory:
            return cls()
        try:
            raw = (Path(directory) / "configuration.json").read_bytes()
        except FileNotFoundError:
            return cls()
        return cls.model_validate(json.loads(raw)["values"])
