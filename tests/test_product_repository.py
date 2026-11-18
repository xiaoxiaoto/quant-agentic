"""产品规则与参数仓储单元测试。"""

from src.contracts.domains.product import FeeRule, ProductConfig, TradeRule
from src.repositories.product import ProductRepository


def test_product_repository_defaults() -> None:
    repo = ProductRepository()
    config = repo.get_product_config("GOLD_ACCUMULATION")
    assert config is not None
    assert config.instrument_id == "GOLD_ACCUMULATION"
    assert config.fee_rule.sell_fee_rate == 0.004
    assert config.trade_rule.buy_min_grams == 1.0


def test_register_and_list_products() -> None:
    repo = ProductRepository()
    custom_cfg = ProductConfig(
        instrument_id="CUSTOM_GOLD",
        config_version="2.0.0",
        trade_rule=TradeRule(buy_min_grams=5.0),
        fee_rule=FeeRule(sell_fee_rate=0.002),
    )
    repo.register_product_config(custom_cfg)
    products = repo.list_products()
    assert "GOLD_ACCUMULATION" in products
    assert "CUSTOM_GOLD" in products

    fetched = repo.get_product_config("CUSTOM_GOLD")
    assert fetched is not None
    assert fetched.trade_rule.buy_min_grams == 5.0
    assert fetched.fee_rule.sell_fee_rate == 0.002
