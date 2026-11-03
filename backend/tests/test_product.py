from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

import pytest

from videotutor.core.product import Product, ProductFileError, load_product, parse_product

from .conftest import REPO


def _raw() -> dict[str, Any]:
    with (REPO / "product.toml").open("rb") as f:
        return tomllib.load(f)


def test_real_product_file_is_valid(product: Product) -> None:
    assert product.name == "VideoTutor"
    assert len(product.version_tuple) == 3
    assert product.ports[0] == product.port_first


def test_extension_id_matches_the_documented_one(product: Product) -> None:
    # The ID is written in product.toml's comment and relied on by users'
    # installed extensions; a key change would silently orphan them.
    assert product.extension_id == "kedjniaglmeoobkgdeflonfmlpihmndm"
    assert product.extension_origin == "chrome-extension://kedjniaglmeoobkgdeflonfmlpihmndm"


@pytest.mark.parametrize(
    ("section", "key", "value", "message"),
    [
        ("product", "version", "1.0", "not X.Y.Z"),
        ("product", "app_id", "{lowercase-or-bad}", "GUID"),
        ("product", "name", 3, "must be str"),
        ("runtime", "port_first", True, "must be int"),
        ("runtime", "port_last", 80, "port range"),
        ("runtime", "protocol", 0, "protocol"),
        ("extension", "public_key", "not base64!!", "base64"),
    ],
)
def test_invalid_fields_are_rejected(section: str, key: str, value: object, message: str) -> None:
    raw = copy.deepcopy(_raw())
    raw[section][key] = value
    with pytest.raises(ProductFileError, match=message):
        parse_product(raw)


def test_missing_field_and_section() -> None:
    raw = copy.deepcopy(_raw())
    del raw["product"]["publisher"]
    with pytest.raises(ProductFileError, match="missing"):
        parse_product(raw)
    raw = copy.deepcopy(_raw())
    del raw["runtime"]
    with pytest.raises(ProductFileError, match="needs"):
        parse_product(raw)


def test_load_reports_unreadable_and_malformed_files(tmp_path: Path) -> None:
    with pytest.raises(ProductFileError, match="cannot read"):
        load_product(tmp_path / "absent.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[product\n", encoding="utf-8")
    with pytest.raises(ProductFileError, match="not valid TOML"):
        load_product(bad)
