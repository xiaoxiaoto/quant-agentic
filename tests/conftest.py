"""本地测试不依赖外部模型服务或个人密钥。"""

import os

# 收集阶段会导入应用入口，测试进程显式关闭需要外部资源的默认模型。
os.environ["TIMESFM_ENABLED"] = "false"
os.environ["NEWS_LLM_ENABLED"] = "false"
os.environ["LIGHTGBM_MODEL_DIR"] = ""
