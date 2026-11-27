"""领域异常定义，用于屏蔽底层存储与外部基础设施实现细节。"""


class StorageError(RuntimeError):
    """持久化存储读写失败时抛出的统一领域异常。"""


class QlibUnavailableError(RuntimeError):
    """未安装可选依赖 pyqlib 或 Qlib 运行时初始化失败时抛出的领域异常。"""


class QlibDatasetError(ValueError):
    """Qlib 数据集目录无效、样本不足或导出参数不合法时抛出的领域异常。"""


class TimesFMDatasetError(ValueError):
    """TimesFM 数据集无效、样本不足、回测窗口越界或导出参数不合法时抛出的领域异常。"""


class TimesFMTrainingError(RuntimeError):
    """TimesFM 推理特征提取、微调或融合头训练失败时抛出的领域异常。"""
