"""交易枚举项及 (value, description) 键值对单元测试。"""

import inspect

import src.types.enums.trading as trading_enums
from src.types.enums.trading import (
    AlertState,
    DataQualityState,
    DecisionAction,
    DescribedStrEnum,
    GateResult,
    HoldingPeriod,
    OrderState,
    RiskState,
    TradeDirection,
    ValidationLevel,
)


def test_described_str_enum_attributes() -> None:
    # 验证基础枚举属性
    assert TradeDirection.BUY.value == "BUY"
    assert TradeDirection.BUY.description == "买入"
    assert TradeDirection.BUY.desc == "买入"
    assert TradeDirection.BUY.pair == ("BUY", "买入")
    assert str(TradeDirection.BUY) == "BUY"
    assert TradeDirection.BUY == "BUY"


def test_all_enums_have_value_and_description() -> None:
    # 动态遍历所有枚举类与枚举成员，验证均具备非空 value 与非空中文字符串描述
    enum_classes: list[type[DescribedStrEnum]] = [
        obj
        for _, obj in inspect.getmembers(trading_enums, inspect.isclass)
        if issubclass(obj, DescribedStrEnum) and obj is not DescribedStrEnum
    ]

    assert len(enum_classes) == 9  # 共有 9 个业务枚举类

    for cls in enum_classes:
        for member in cls:
            assert isinstance(member, DescribedStrEnum)
            assert hasattr(member, "value"), f"{cls.__name__}.{member.name} 缺少 value"
            assert hasattr(member, "description"), f"{cls.__name__}.{member.name} 缺少 description"
            assert isinstance(member.value, str)
            assert len(member.value) > 0
            assert isinstance(member.description, str)
            assert len(member.description) > 0
            assert member.pair == (member.value, member.description)


def test_key_enum_mappings() -> None:
    assert RiskState.HALT_RECONCILE.pair == ("HALT_RECONCILE", "对账差异紧急熔断停机")
    assert DecisionAction.ABSTAIN.pair == ("ABSTAIN", "弃权不操作")
    assert DataQualityState.ANOMALY.pair == ("ANOMALY", "异常跳变或畸变")
    assert OrderState.UNKNOWN.pair == ("UNKNOWN", "状态未知需查单核对")
    assert AlertState.DISCREPANCY.pair == ("DISCREPANCY", "成交对账发现差异")
    assert GateResult.PASS.pair == ("PASS", "准入闸门通过")
    assert HoldingPeriod.W1.pair == ("1w", "1周周期")
    assert ValidationLevel.V1_BANK_REPLAY.pair == ("V1", "银行报价历史回放")
