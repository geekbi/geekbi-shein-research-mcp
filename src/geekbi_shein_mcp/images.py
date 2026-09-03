"""图搜同款的图片读取、校验与 multipart 组装。"""

from __future__ import annotations

import base64
import binascii
import mimetypes
import os
import re
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, url2pathname, urlopen


MAX_IMAGE_SIZE = 10 * 1024 * 1024


def _content_type(value: str | None) -> str | None:
    return value.split(";", 1)[0].strip().lower() if value else None


def _detect(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in {b"avif", b"avis"}:
            return "image/avif"
        if brand in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
            return "image/heic"
    return None


def _extension(content_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/tiff": ".tiff",
        "image/avif": ".avif",
        "image/heic": ".heic",
        "image/heif": ".heic",
    }.get(content_type, ".img")


def _validate(data: bytes, declared: str | None = None) -> str:
    if not data:
        raise ValueError("图片内容为空")
    if len(data) > MAX_IMAGE_SIZE:
        raise ValueError("图片大小不能超过 10MB")
    detected = _detect(data)
    declared = _content_type(declared)
    if detected:
        return detected
    if declared and declared.startswith("image/"):
        return declared
    raise ValueError("无法识别图片格式，请使用常见图片文件")


def _decode_base64(encoded: str, declared: str | None = None) -> tuple[bytes, str, str]:
    if len(encoded) > (MAX_IMAGE_SIZE * 4 // 3) + 16:
        raise ValueError("图片大小不能超过 10MB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("图片 Base64 内容无效") from error
    content_type = _validate(data, declared)
    return data, content_type, "image" + _extension(content_type)


def _read_data_uri(source: str) -> tuple[bytes, str, str]:
    match = re.fullmatch(
        r"data:([^;,]+)?(?:;charset=[^;,]+)?;base64,(.+)",
        source,
        re.DOTALL,
    )
    if not match:
        raise ValueError("图片 Data URI 格式不正确")
    return _decode_base64(match.group(2), match.group(1))


def _read_remote(source: str, timeout: float) -> tuple[bytes, str, str]:
    request = Request(source, headers={"User-Agent": "GeekBI-SHEIN-Research-MCP"})
    with urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_IMAGE_SIZE + 1)
        content_type = _validate(data, response.headers.get_content_type())
        filename = Path(unquote(urlparse(response.geturl()).path)).name
        return data, content_type, filename or "image" + _extension(content_type)


def _read_local(source: str) -> tuple[bytes, str, str]:
    parsed = urlparse(source)
    if parsed.scheme == "file":
        path_text = unquote(parsed.path)
        if parsed.netloc and parsed.netloc != "localhost":
            path_text = f"//{parsed.netloc}{path_text}"
        path_text = url2pathname(path_text)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", path_text):
            path_text = path_text[1:]
        path = Path(path_text)
    else:
        path = Path(source).expanduser()
    if not path.is_file():
        raise ValueError(f"找不到图片文件：{path}")
    if path.stat().st_size > MAX_IMAGE_SIZE:
        raise ValueError("图片大小不能超过 10MB")
    data = path.read_bytes()
    guessed, _ = mimetypes.guess_type(path.name)
    return data, _validate(data, guessed), path.name


def read_image(source: str, timeout: float) -> tuple[bytes, str, str]:
    if source.startswith("//"):
        source = "https:" + source
    if source.startswith("data:"):
        return _read_data_uri(source)
    if source.startswith("base64:"):
        return _decode_base64(source.removeprefix("base64:"))
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return _read_remote(source, timeout)
    if parsed.scheme not in {"", "file"}:
        raise ValueError("图片只支持本地文件、file/http/https 地址、Data URI 或 Base64")
    return _read_local(source)


def build_multipart(data: bytes, content_type: str, filename: str) -> tuple[bytes, str]:
    boundary = "----GeekBIBoundary" + uuid.uuid4().hex
    safe_filename = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)
    if not safe_filename:
        safe_filename = "image" + _extension(content_type)
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    body = header + data + f"\r\n--{boundary}--\r\n".encode("ascii")
    return body, f"multipart/form-data; boundary={boundary}"
