"""本地测试不依赖外部模型服务或个人密钥。"""

import os

# 收集阶段会导入应用入口，测试进程显式关闭需要外部资源的默认模型。

# 模型推理
os.environ["TIMESFM_ENABLED"] = "false"
os.environ["LIGHTGBM_MODEL_DIR"] = ""

# 新闻 Agent
os.environ["NEWS_ENABLED"] = "false"

# 京东业务
os.environ["LIVE_QUOTES_ENABLED"] = "false"
os.environ["JD_PROACTIVE_AUTH_ENABLED"] = "false"
os.environ["SYNTHETIC_PAPER_QUOTE_ENABLED"] = "false"
