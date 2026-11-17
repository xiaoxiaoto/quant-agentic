"""黄金积存金产品规则与费率参数仓储。

管理各产品规则、买卖交易规则、费率参数与计息规则的版本化配置。
"""

from src.types.domain.product import (
    FeeRule,
    InterestRule,
    ProductConfig,
    SessionConfig,
    TradeRule,
)


class ProductRepository:
    """产品规则与参数仓储。

    存储并提供各黄金产品的交易规则与费率版本，支持动态调整与审计追溯。
    """

    def __init__(self) -> None:
        self._configs: dict[str, ProductConfig] = {}
        self._init_defaults()

    def _init_defaults(self) -> None:
        """初始化默认产品配置。"""
        default_config = ProductConfig(
            instrument_id="GOLD_ACCUMULATION",
            config_version="1.0.0",
            trade_rule=TradeRule(
                buy_min_grams=1.0,
                sell_min_grams=1.0,
                liquidate_all_if_le_1g=True,
            ),
            fee_rule=FeeRule(
                buy_fee_rate=0.0,
                sell_fee_rate=0.004,
            ),
            interest_rule=InterestRule(
                annual_rate=0.0002,
                backtest_rate=0.0,
            ),
            session_config=SessionConfig(
                timezone="Asia/Shanghai",
            ),
        )
        self._configs[default_config.instrument_id] = default_config

    def get_product_config(self, instrument_id: str = "GOLD_ACCUMULATION") -> ProductConfig | None:
        """获取指定产品代码的当前生效配置。"""
        return self._configs.get(instrument_id)

    def register_product_config(self, config: ProductConfig) -> None:
        """注册或更新产品配置版本。"""
        self._configs[config.instrument_id] = config

    def list_products(self) -> list[str]:
        """返回所有已注册产品代码列表。"""
        return list(self._configs.keys())
