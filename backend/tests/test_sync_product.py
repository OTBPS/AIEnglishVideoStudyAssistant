from __future__ import annotations

import json

import sync_product  # tools/ is on pythonpath


def test_repository_is_in_sync() -> None:
    assert sync_product.main(["--check"]) == 0


def test_manifest_takes_identity_from_product() -> None:
    product = sync_product.load()
    out = json.loads(
        sync_product.render_manifest(
            product, {"name": "old", "version": "0.0.0", "permissions": ["x"]}
        )
    )
    assert out["version"] == product["product"]["version"]
    assert out["key"] == product["extension"]["public_key"]
    assert out["permissions"] == ["x"]  # hand-written fields are preserved


def test_product_js_is_generated() -> None:
    js = sync_product.render_product_js(sync_product.load())
    assert js.startswith("// GENERATED")
    assert '"protocol": 1' in js
