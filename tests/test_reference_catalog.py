"""参考行情品种清单、单位和分产品日志恢复测试。"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.contracts.domains.reference_quote import ReferenceProductCatalog, ReferenceQuote
from src.repositories.reference_quote import ReferenceQuoteRepository


def quote(code: str, price: float = 800.0) -> ReferenceQuote:
    product = ReferenceProductCatalog.get(code)
    return ReferenceQuote(
        quote_id=f"{code}:{price}",
        source_id="JD_REFERENCE",
        product_code=code,
        unit=product.unit,
        price_side=product.price_side,
        price=price,
        quote_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        payload_hash="a" * 64,
    )


def test_catalog_covers_all_banks_and_precious_metals_with_explicit_units() -> None:
    products = ReferenceProductCatalog.all()
    assert len(products) == 28
    assert len({product.code for product in products}) == len(products)
    assert sum(product.category == "bank" for product in products) == 6
    assert sum(product.category == "sge" for product in products) == 17
    assert ReferenceProductCatalog.get("SGE-Ag(T+D)").unit == "CNY/kg"
    assert ReferenceProductCatalog.get("SGE-Ag99.99").unit == "CNY/kg"
    assert ReferenceProductCatalog.get("WG-XAUUSD").unit == "USD/oz"
    assert ReferenceProductCatalog.get("SGE-NYAuTN06").unit == "CNY/g"
    assert ReferenceProductCatalog.get("CIB-JCJ0").price_side == "buy"
    assert ReferenceProductCatalog.get("CMBC-JCJ").price_side == "reference"
    with pytest.raises(ValueError):
        ReferenceProductCatalog.get("unknown")


def test_repository_restores_latest_per_product(tmp_path: Path) -> None:
    repository = ReferenceQuoteRepository(tmp_path)
    repository.save(quote("CMBC-JCJ", 800))
    repository.save(quote("CZB-JCJ", 801))
    repository.save(quote("CMBC-JCJ", 802))
    restored = ReferenceQuoteRepository(tmp_path)
    cmbc = restored.latest()
    czb = restored.latest("CZB-JCJ")
    assert cmbc is not None
    assert cmbc.price == 802
    assert czb is not None
    assert czb.price == 801
    assert restored.latest("WG-XAUUSD") is None
    assert set(restored.latest_all()) == {"CMBC-JCJ", "CZB-JCJ"}


def test_repository_restores_legacy_hash_without_price_side(tmp_path: Path) -> None:
    legacy = quote("CMBC-JCJ").model_dump(mode="json", exclude={"record_hash", "price_side"})
    data = json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    legacy["record_hash"] = hashlib.sha256(data.encode()).hexdigest()
    (tmp_path / "references_2026-11-18.jsonl").write_text(json.dumps(legacy) + "\n")
    restored = ReferenceQuoteRepository(tmp_path).latest()
    assert restored is not None
    assert restored.price_side == "reference"
    assert restored.price == 800


def test_repository_rejects_tampering_across_products(tmp_path: Path) -> None:
    repository = ReferenceQuoteRepository(tmp_path, write_jsonl=True)
    repository.save(quote("CMBC-JCJ", 800))
    repository.save(quote("CIB-JCJ0", 801))
    path = next(tmp_path.glob("*.jsonl"))
    path.write_text(path.read_text().replace('"price_side":"buy"', '"price_side":"reference"'))
    with pytest.raises(ValueError, match="完整性"):
        repository.latest_all()


def test_repository_latest_all_stops_early_when_targets_found(tmp_path: Path) -> None:
    repository = ReferenceQuoteRepository(tmp_path)
    # 写入一个较早日期的损坏文件；若目标品种已在最新文件中找齐，则不会扫描旧文件。
    (tmp_path / "references_2020-01-01.jsonl").write_text("{corrupt\n", encoding="utf-8")
    repository.save(quote("CMBC-JCJ", 805))
    restored = repository.latest_all(target_products=("CMBC-JCJ",))
    assert set(restored) == {"CMBC-JCJ"}
    assert restored["CMBC-JCJ"].price == 805
