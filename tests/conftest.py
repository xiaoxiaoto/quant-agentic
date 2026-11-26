"""本地测试不依赖外部模型服务或个人密钥。"""

import os
from tempfile import TemporaryDirectory

# 收集阶段会导入应用入口，默认应用使用临时数据目录和离线配置。
_TEST_DATA_DIR = TemporaryDirectory(prefix="quant-agentic-tests-")
os.environ["DATA_DIR"] = _TEST_DATA_DIR.name

# 模型推理
os.environ["TIMESFM_ENABLED"] = "false"
os.environ["LIGHTGBM_MODEL_DIR"] = ""

# 新闻 Agent
os.environ["NEWS_ENABLED"] = "false"
os.environ["NEWS_SYNC_ENABLED"] = "false"

# 京东业务
os.environ["LIVE_QUOTES_ENABLED"] = "false"
os.environ["MARKET_BARS_ENABLED"] = "false"
os.environ["JD_HOLDINGS_SYNC_ENABLED"] = "false"
os.environ["JD_PROACTIVE_AUTH_ENABLED"] = "false"
os.environ["SYNTHETIC_PAPER_QUOTE_ENABLED"] = "false"
os.environ["QLIB_ENABLED"] = "false"
