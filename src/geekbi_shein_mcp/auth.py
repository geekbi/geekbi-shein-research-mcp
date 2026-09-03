"""极鲸云网页登录状态与受保护请求封装。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from platformdirs import user_config_path

from . import __version__


TOKEN_ENDPOINT = "/api/v1/agent/auth/token"
VERSION_HEADER = "X-GeekBI-MCP-Version"
STATE_VERSION = 1
_PROCESS_LOCK = threading.RLock()


class GeekBIError(Exception):
    """可直接向用户展示中文消息的极鲸云请求错误。"""


class ActionRequired(Exception):
    def __init__(
        self,
        message: str,
        jump_url: str = "",
        *,
        action: str = "ACTION_REQUIRED",
        expires_in: int = 0,
        pending: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.jump_url = jump_url
        self.action = action
        self.expires_in = max(0, expires_in)
        self.pending = pending

    def public_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "actionRequired": True,
            "actionPending": self.pending,
            "action": self.action,
            "msg": self.message,
            "expiresIn": self.expires_in,
        }
        if self.jump_url:
            payload["jumpUrl"] = self.jump_url
        return payload


def response_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        message = payload.get("msg")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return fallback


def default_auth_path() -> Path:
    return (
        user_config_path("GeekBI", appauthor=False, ensure_exists=True)
        / "shein-research-mcp"
        / "agent-auth.json"
    )


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "servers": {}}


def _normalize_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _empty_state()
    servers = payload.get("servers")
    if not isinstance(servers, dict):
        servers = {}
    return {
        "version": STATE_VERSION,
        "servers": {
            key: value
            for key, value in servers.items()
            if isinstance(key, str) and isinstance(value, dict)
        },
    }


def _restrict_permissions(path: Path, mode: int) -> None:
    if os.name != "posix":
        return
    try:
        path.chmod(mode)
    except (OSError, AttributeError, NotImplementedError):
        pass


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(path.parent, 0o700)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+b") as handle:
        _restrict_permissions(lock_path, 0o600)
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _empty_state()
    try:
        return _normalize_state(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise GeekBIError("无法读取极鲸云登录状态") from error


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(path.parent, 0o700)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".agent-auth-",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(_normalize_state(payload), handle, ensure_ascii=False)
            handle.flush()
            if hasattr(os, "fsync"):
                os.fsync(handle.fileno())
        _restrict_permissions(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
        _restrict_permissions(path, 0o600)
    except OSError as error:
        raise GeekBIError("无法保存极鲸云登录状态") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


class AuthManager:
    def __init__(self, base_url: str, state_path: Path | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.state_path = state_path or default_auth_path()

    @property
    def server_key(self) -> str:
        return self.base_url

    def _mutate(
        self,
        callback: Callable[[dict[str, Any]], tuple[bool, Any]],
    ) -> Any:
        with _PROCESS_LOCK, _file_lock(self.state_path):
            payload = _read_state(self.state_path)
            changed, result = callback(payload)
            if changed:
                _write_state(self.state_path, payload)
            return result

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"GeekBI-SHEIN-Research-MCP/{__version__}",
            VERSION_HEADER: __version__,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    @staticmethod
    def _read_json_response(response: Any) -> dict[str, Any]:
        try:
            payload = json.loads(response.read().decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise GeekBIError("极鲸云返回了无法解析的数据") from error
        if not isinstance(payload, dict):
            raise GeekBIError("极鲸云返回的数据格式不正确")
        return payload

    @staticmethod
    def _read_http_error(error: HTTPError) -> dict[str, Any] | None:
        try:
            payload = json.loads(error.read().decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _clear_expired(server: dict[str, Any], now: int) -> bool:
        changed = False
        if server.get("accessToken") and int(server.get("accessTokenExpiresAt", 0)) <= now:
            server.pop("accessToken", None)
            server.pop("accessTokenExpiresAt", None)
            changed = True
        pending = server.get("pending")
        if isinstance(pending, dict) and int(pending.get("expiresAt", 0)) <= now:
            server.pop("pending", None)
            changed = True
        return changed

    @staticmethod
    def _raise_action_if_needed(payload: Any) -> None:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return
        jump_url = data.get("jumpUrl")
        if not isinstance(jump_url, str) or not jump_url:
            return
        raise ActionRequired(
            response_message(payload, "请完成页面操作后继续"),
            jump_url,
            action=str(data.get("error") or "ACTION_REQUIRED"),
            expires_in=int(data.get("expiresIn", 0)),
        )

    def _authorization_header(self) -> str | None:
        now = int(time.time())

        def read_token(payload: dict[str, Any]) -> tuple[bool, str | None]:
            server = payload["servers"].get(self.server_key)
            if not isinstance(server, dict):
                return False, None
            changed = self._clear_expired(server, now)
            token = server.get("accessToken")
            return changed, f"Bearer {token}" if isinstance(token, str) and token else None

        return self._mutate(read_token)

    def _save_challenge(self, payload: dict[str, Any]) -> None:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise GeekBIError(response_message(payload, "需要登录后继续"))
        device_code = data.get("deviceCode")
        jump_url = data.get("jumpUrl")
        if not isinstance(device_code, str) or not device_code:
            raise GeekBIError("登录响应缺少设备码")
        if not isinstance(jump_url, str) or not jump_url:
            raise GeekBIError("登录响应缺少跳转地址")
        expires_in = int(data.get("expiresIn", 0))

        def save(state: dict[str, Any]) -> tuple[bool, None]:
            server = state["servers"].setdefault(self.server_key, {})
            server.pop("accessToken", None)
            server.pop("accessTokenExpiresAt", None)
            server["pending"] = {
                "deviceCode": device_code,
                "jumpUrl": jump_url,
                "expiresAt": int(time.time()) + expires_in,
            }
            return True, None

        self._mutate(save)
        raise ActionRequired(
            response_message(payload, "需要登录后继续"),
            jump_url,
            action="AUTH_REQUIRED",
            expires_in=expires_in,
        )

    def complete_pending_login(self, timeout: float) -> bool:
        now = int(time.time())

        def read_pending(state: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
            server = state["servers"].get(self.server_key)
            if not isinstance(server, dict):
                return False, None
            changed = self._clear_expired(server, now)
            pending = server.get("pending")
            return changed, dict(pending) if isinstance(pending, dict) else None

        pending = self._mutate(read_pending)
        if pending is None:
            return False

        request = Request(
            f"{self.base_url}{TOKEN_ENDPOINT}",
            data=json.dumps({"deviceCode": pending["deviceCode"]}).encode("utf-8"),
            headers=self._headers("application/json"),
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = self._read_json_response(response)
        except HTTPError as error:
            payload = self._read_http_error(error)
            data = payload.get("data") if isinstance(payload, dict) else None
            error_code = data.get("error") if isinstance(data, dict) else None
            if error.code == 202 or error_code == "AUTHORIZATION_PENDING":
                raise ActionRequired(
                    "等待用户完成网页登录。完成后请再次调用原查询。",
                    str(pending.get("jumpUrl") or ""),
                    action="AUTH_REQUIRED",
                    expires_in=max(0, int(pending.get("expiresAt", now)) - now),
                    pending=True,
                ) from error
            if error.code in (400, 410) or error_code in {
                "INVALID_DEVICE_CODE",
                "AUTHORIZATION_EXPIRED",
            }:
                self._remove_pending(str(pending.get("deviceCode") or ""))
                return False
            raise GeekBIError(response_message(payload, "登录状态查询失败")) from error

        data = payload.get("data")
        if isinstance(data, dict) and data.get("error") == "AUTHORIZATION_PENDING":
            raise ActionRequired(
                "等待用户完成网页登录。完成后请再次调用原查询。",
                str(pending.get("jumpUrl") or ""),
                action="AUTH_REQUIRED",
                expires_in=max(0, int(pending.get("expiresAt", now)) - now),
                pending=True,
            )
        if payload.get("code") != 0 or not isinstance(data, dict):
            raise GeekBIError(response_message(payload, "登录令牌响应异常"))
        access_token = data.get("accessToken")
        if not isinstance(access_token, str) or not access_token:
            raise GeekBIError("登录令牌响应缺少访问令牌")
        expires_in = int(data.get("expiresIn", 0))

        def save_token(state: dict[str, Any]) -> tuple[bool, bool]:
            server = state["servers"].get(self.server_key)
            if not isinstance(server, dict):
                return False, False
            latest = server.get("pending")
            if not isinstance(latest, dict) or latest.get("deviceCode") != pending.get("deviceCode"):
                return False, False
            server["accessToken"] = access_token
            server["accessTokenExpiresAt"] = now + max(0, expires_in - 30)
            server.pop("pending", None)
            return True, True

        return bool(self._mutate(save_token))

    def _remove_pending(self, device_code: str) -> None:
        def remove(state: dict[str, Any]) -> tuple[bool, None]:
            server = state["servers"].get(self.server_key)
            if not isinstance(server, dict):
                return False, None
            pending = server.get("pending")
            if not isinstance(pending, dict) or pending.get("deviceCode") != device_code:
                return False, None
            server.pop("pending", None)
            return True, None

        self._mutate(remove)

    def request_json(
        self,
        url: str,
        timeout: float,
        *,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.complete_pending_login(timeout)
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        authorization = self._authorization_header()
        if authorization:
            request_headers["token"] = authorization
        request = Request(url, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = self._read_json_response(response)
        except HTTPError as error:
            payload = self._read_http_error(error)
            data = payload.get("data") if isinstance(payload, dict) else None
            if error.code == 401 and isinstance(data, dict) and data.get("error") == "AUTH_REQUIRED":
                self._save_challenge(payload)
            self._raise_action_if_needed(payload)
            raise GeekBIError(response_message(payload, "极鲸云请求失败")) from error
        self._raise_action_if_needed(payload)
        return payload
