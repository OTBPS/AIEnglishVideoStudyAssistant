"""Product identity, read from product.toml -- the single source of truth.

Nothing else in the code base may spell out the version, the app name, the
installer AppId or the port range. The loader validates eagerly: a broken
product file must fail at startup with a clear message, not as a confusing
error three modules away.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_GUID = re.compile(r"^\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}$")


class ProductFileError(ValueError):
    """product.toml is missing, unreadable or invalid."""


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    display_name: str
    version: str
    publisher: str
    repository: str
    app_id: str
    port_first: int
    port_last: int
    protocol: int
    single_instance_mutex: str
    extension_public_key: str

    @property
    def ports(self) -> range:
        return range(self.port_first, self.port_last + 1)

    @property
    def version_tuple(self) -> tuple[int, int, int]:
        major, minor, patch = (int(p) for p in self.version.split("."))
        return major, minor, patch

    @property
    def extension_id(self) -> str:
        """Chrome derives an unpacked extension's ID from its public key."""
        der = base64.b64decode(self.extension_public_key)
        digest = hashlib.sha256(der).hexdigest()[:32]
        return "".join(chr(ord("a") + int(c, 16)) for c in digest)

    @property
    def extension_origin(self) -> str:
        return f"chrome-extension://{self.extension_id}"


def _require(table: dict[str, Any], key: str, kind: type, where: str) -> Any:
    if key not in table:
        raise ProductFileError(f"product.toml: missing [{where}].{key}")
    value = table[key]
    # bool is an int subclass; never accept it where a number is expected.
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ProductFileError(
            f"product.toml: [{where}].{key} must be {kind.__name__}, got {value!r}"
        )
    return value


def parse_product(data: dict[str, Any]) -> Product:
    p = data.get("product")
    r = data.get("runtime")
    e = data.get("extension")
    if not isinstance(p, dict) or not isinstance(r, dict) or not isinstance(e, dict):
        raise ProductFileError("product.toml: needs [product], [runtime] and [extension]")

    product = Product(
        id=_require(p, "id", str, "product"),
        name=_require(p, "name", str, "product"),
        display_name=_require(p, "display_name", str, "product"),
        version=_require(p, "version", str, "product"),
        publisher=_require(p, "publisher", str, "product"),
        repository=_require(p, "repository", str, "product"),
        app_id=_require(p, "app_id", str, "product"),
        port_first=_require(r, "port_first", int, "runtime"),
        port_last=_require(r, "port_last", int, "runtime"),
        protocol=_require(r, "protocol", int, "runtime"),
        single_instance_mutex=_require(r, "single_instance_mutex", str, "runtime"),
        extension_public_key=_require(e, "public_key", str, "extension"),
    )

    if not _SEMVER.match(product.version):
        raise ProductFileError(f"product.toml: version {product.version!r} is not X.Y.Z")
    if not _GUID.match(product.app_id):
        raise ProductFileError(f"product.toml: app_id {product.app_id!r} is not an upper-case GUID")
    if not 1024 <= product.port_first <= product.port_last <= 65535:
        raise ProductFileError("product.toml: port range must satisfy 1024 <= first <= last")
    if product.protocol < 1:
        raise ProductFileError("product.toml: protocol must be >= 1")
    try:
        base64.b64decode(product.extension_public_key, validate=True)
    except binascii.Error as exc:
        raise ProductFileError("product.toml: extension.public_key is not base64") from exc
    return product


def load_product(path: Path) -> Product:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except OSError as exc:
        raise ProductFileError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ProductFileError(f"{path} is not valid TOML: {exc}") from exc
    return parse_product(data)
