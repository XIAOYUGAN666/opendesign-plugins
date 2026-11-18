"""Persistent RFEM gRPC session with auto-reconnect."""

import logging
import re
import threading
from contextlib import contextmanager

logger = logging.getLogger("dlubal_rfem_mcp.rfem")


class RFEMBusy(RuntimeError):
    """The existing software session is executing another request."""


def restriction_category(error: Exception) -> str:
    """Inspect real gRPC causes, never SDK wrapper advice or arbitrary text."""
    import grpc

    seen = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, grpc.RpcError):
            code = current.code()
            details = (current.details() or "").lower()
            if code in {
                grpc.StatusCode.UNAUTHENTICATED,
                grpc.StatusCode.PERMISSION_DENIED,
                grpc.StatusCode.RESOURCE_EXHAUSTED,
                grpc.StatusCode.UNKNOWN,
            }:
                if any(
                    reason in details
                    for reason in (
                        "invalid api key",
                        "api key expired",
                        "api key is invalid",
                    )
                ):
                    return "authenticationFailed"
                if any(
                    reason in details
                    for reason in (
                        "quota exceeded",
                        "quota exhausted",
                        "insufficient credits",
                    )
                ):
                    return "quotaRestricted"
                if any(
                    reason in details
                    for reason in (
                        "permission denied",
                        "not authorized",
                        "authorization denied",
                    )
                ):
                    return "authorizationRestricted"
                if (
                    "dlubal license server closed the connection unexpectedly"
                    in details
                    and "network error:" in details
                ):
                    # RFEM also uses UNAUTHENTICATED for upstream license-network
                    # failures. Its actual detail is not an invalid Key or quota.
                    return "softwareUnavailable"
                return "apiAccessRestricted"
            return "softwareUnavailable"
        current = current.__cause__ or current.__context__
    return (
        "unsupportedSoftwareVersion"
        if isinstance(error, ValueError) and str(error) == "unsupportedSoftwareVersion"
        else "softwareUnavailable"
    )


def rpc_error(error: BaseException):
    import grpc

    seen = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, grpc.RpcError):
            return current
        current = current.__cause__ or current.__context__
    return None


def public_error_text(text: str, credential: str = "") -> str:
    if credential:
        text = text.replace(credential, "[REDACTED]")
    text = re.sub(r"(?im)^([ \t]*(?:set-)?cookie[ \t]*:[ \t]*)[^\r\n]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)\b(?:ak-|sk-)[a-z0-9_-]{12,}\b", "[REDACTED]", text)
    text = re.sub(
        r"""(?i)(["']?(?:[a-z0-9]+[_-])*(?:api[_ -]?key|token|password|passwd|secret|authorization|cookie|credential)["']?[ \t]*[:=][ \t]*)(?:(?:Basic|Bearer)[ \t]+)?(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^\s,;}]+)""",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)(\bBearer[ \t]+)[a-z0-9._~+/=-]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"""(?i)(?<![\w"'])(?:[a-z]:[\\/]|\\\\)[^\r\n"']+""", "[REDACTED]", text)
    text = re.sub(
        r"""(?i)(?:file://[^\s"']+|["'](?:[a-z]:[\\/]|\\\\)[^"'\r\n]*["']|(?<!\w)(?:[a-z]:[\\/]|\\\\)[^\s"'<>]+|(?<![\w/])/(?:home|users|tmp|var|etc|mnt|root|private|opt|run|proc|dev|media|data|usr|volumes|workspace)(?:/[^\s"'<>]*)?)""",
        "[REDACTED]",
        text,
    )
    text = re.sub(r"(?im)^[ \t]*(?:traceback \(most recent call last\):|(?:command|environment|env|headers|cookies)[ \t]*:|File\s+[\"']).*$", "[REDACTED]", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]", "", text)


class RFEMSession:
    """Per-call RFEM connection with a serialising lock."""

    def __init__(
        self,
        api_key_value: str,
        url: str,
        port: int,
        *,
        expected_build: str | None = None,
    ):
        self._lock = threading.Lock()
        if not api_key_value:
            raise ValueError("credentialMissing")
        self._api_key_value = api_key_value
        self._url = url
        self._port = port
        self._expected_build = expected_build
        self.restriction: str | None = None
        self.verified = False

    def describe_error(self, error: BaseException, fallback: str) -> str:
        cause = rpc_error(error)
        if cause is not None:
            try:
                code = cause.code()
                detail = public_error_text(cause.details() or "", self._api_key_value)
                return "\n".join(part for part in (getattr(code, "name", ""), detail) if part)[:8192]
            except (AttributeError, TypeError):
                pass
        return public_error_text(fallback, self._api_key_value)[:8192]

    @contextmanager
    def connection(self, timeout: float = 0.0, *, service_query: bool = False):
        acquired = self._lock.acquire(timeout=timeout)
        if not acquired:
            raise RFEMBusy(
                "RFEM is busy executing another request. Try again when it finishes."
            )
        app = None
        try:
            try:
                from dlubal.api.rfem.application import Application

                class ServiceApplication(Application):
                    # The locked SDK calls this method during construction. Reuse
                    # the response for the single explicit Service query.
                    _application_info = None

                    def get_application_info(self):
                        if self._application_info is None:
                            self._application_info = super().get_application_info()
                        return self._application_info

                application = ServiceApplication if service_query else Application
                app = application(
                    api_key_value=self._api_key_value,
                    url=self._url,
                    port=self._port,
                )
            except Exception as error:
                self.verified = False
                self.restriction = restriction_category(error)
                raise RuntimeError(self.restriction) from error
            if app is None:
                raise RuntimeError("rfemUnavailable")
            if not service_query and self._expected_build is not None:
                # Dlubal exposes the public product version separately from the
                # exact build.  For example, version is ``6.14.0013`` while
                # full_version starts with ``6.14.0013.249.`` and then carries
                # the source revision.  Host compatibility is pinned to those
                # first four full-version components.
                self.validate_application_info(app.get_application_info())
            self.restriction = None
            self.verified = True
            yield app
        finally:
            try:
                if app is not None:
                    app.close_connection()
            except Exception as exc:
                from dlubal_rfem_mcp.rfem.execution import ExecutionUncertain

                raise ExecutionUncertain(
                    "RFEM connection cleanup failed; execution outcome requires inspection."
                ) from exc
            finally:
                self._lock.release()

    def validate_application_info(self, info) -> None:
        expected = self._expected_build
        full_version = str(getattr(info, "full_version", "") or "")
        if expected is not None and full_version != expected and not full_version.startswith(expected + "."):
            self.restriction = "unsupportedSoftwareVersion"
            self.verified = False
            raise ValueError("unsupportedSoftwareVersion")
