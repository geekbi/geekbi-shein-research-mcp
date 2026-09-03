"""Authenticated GeekBI OpenAPI client."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode, urlparse

from .auth import AuthManager, GeekBIError, response_message


DEFAULT_BASE_URL = "https://openapi.geekbi.com"


def _environment_timeout() -> float:
    raw = os.getenv("GEEKBI_REQUEST_TIMEOUT", "45").strip()
    try:
        timeout = float(raw)
    except ValueError as error:
        raise GeekBIError("GEEKBI_REQUEST_TIMEOUT 必须是数字") from error
    if timeout <= 0 or timeout > 300:
        raise GeekBIError("请求超时时间必须大于 0 且不超过 300 秒")
    return timeout


def _base_url() -> str:
    value = os.getenv("GEEKBI_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise GeekBIError("GEEKBI_BASE_URL 必须是有效的 HTTP 或 HTTPS 地址")
    return value


def _normalize(value: str) -> str:
    normalized = value.strip().casefold()
    for suffix in ("站点", "站"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    return normalized


class GeekBIClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout if timeout is not None else _environment_timeout()
        self.auth = AuthManager(self.base_url)

    def _url(self, endpoint: str, params: list[tuple[str, str]] | None = None) -> str:
        url = f"{self.base_url}{endpoint}"
        query = urlencode(params or [])
        return f"{url}?{query}" if query else url

    @staticmethod
    def _success(payload: Any, label: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise GeekBIError(f"{label}响应格式不正确")
        if payload.get("code") != 0:
            raise GeekBIError(response_message(payload, f"{label}失败"))
        if "data" not in payload or not isinstance(payload.get("data"), (dict, list)):
            raise GeekBIError(f"{label}响应缺少数据")
        return {"code": 0, "data": payload["data"]}

    def query(self, endpoint: str, query: Any, label: str) -> dict[str, Any]:
        payload = self.auth.request_json(
            self._url(endpoint, query.query_items()),
            self.timeout,
        )
        return self._success(payload, label)

    def image(self, endpoint: str, query: Any, label: str) -> dict[str, Any]:
        from .images import build_multipart, read_image

        data, content_type, filename = read_image(query.image, max(self.timeout, 60))
        body, multipart_type = build_multipart(data, content_type, filename)
        payload = self.auth.request_json(
            self._url(endpoint, query.query_items(exclude={"image"})),
            max(self.timeout, 60),
            method="POST",
            body=body,
            headers={"Content-Type": multipart_type},
        )
        return self._success(payload, label)

    def sites(self, endpoint: str, country: str | None = None) -> dict[str, Any]:
        payload = self.auth.request_json(self._url(endpoint), self.timeout)
        result = self._success(payload, "站点查询")
        data = result["data"]
        sites = data if isinstance(data, list) else data.get("list")
        if not isinstance(sites, list):
            raise GeekBIError("站点查询响应缺少站点列表")
        valid_sites = [site for site in sites if isinstance(site, dict)]
        if country is None or not country.strip():
            return {"code": 0, "data": {"list": valid_sites}}
        target = _normalize(country)
        aliases = (
            "siteId", "siteUID", "siteUid", "id", "name", "cnName", "country",
            "countryName", "code", "regionCode", "domain", "shortName",
        )
        matches = []
        for site in valid_sites:
            values = {_normalize(str(site[key])) for key in aliases if site.get(key) not in (None, "")}
            if target in values:
                matches.append(site)
        if not matches:
            raise GeekBIError("未找到该站点，请确认国家、地区、站点代码或站点 ID")
        return {"code": 0, "data": {"matches": matches}}
