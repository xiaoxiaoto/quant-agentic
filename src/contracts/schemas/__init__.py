"""接口请求与响应数据模型统一管理包。

将各路由模块中散落的 Pydantic Request/Response 模型集中收归此包，
路由层仅保留端点定义与调用逻辑，不再定义数据结构。
"""

from src.contracts.schemas.decision import (
    DecisionEvaluateRequest as DecisionEvaluateRequest,
)
from src.contracts.schemas.decision import (
    DecisionEvaluateResponse as DecisionEvaluateResponse,
)
from src.contracts.schemas.jd_auth import (
    JDAuthActionResponse as JDAuthActionResponse,
)
from src.contracts.schemas.jd_auth import (
    JDAuthLoginResponse as JDAuthLoginResponse,
)
from src.contracts.schemas.jd_auth import (
    JDAuthStatusResponse as JDAuthStatusResponse,
)
from src.contracts.schemas.model import (
    MacroEventCreateRequest as MacroEventCreateRequest,
)
from src.contracts.schemas.model import (
    NewsExtractRequest as NewsExtractRequest,
)
from src.contracts.schemas.model import (
    NewsExtractResponse as NewsExtractResponse,
)
from src.contracts.schemas.model import (
    NewsOverviewResponse as NewsOverviewResponse,
)
from src.contracts.schemas.model import (
    NewsSyncReport as NewsSyncReport,
)
from src.contracts.schemas.model import (
    TrainingPipelineReport as TrainingPipelineReport,
)
from src.contracts.schemas.model import (
    TrainingPipelineRequest as TrainingPipelineRequest,
)
from src.contracts.schemas.model import (
    WalkForwardFoldReport as WalkForwardFoldReport,
)
from src.contracts.schemas.qlib import (
    QlibExperimentListResponse as QlibExperimentListResponse,
)
from src.contracts.schemas.qlib import (
    QlibExportReport as QlibExportReport,
)
from src.contracts.schemas.qlib import (
    QlibExportRequest as QlibExportRequest,
)
from src.contracts.schemas.qlib import (
    QlibFactorEvaluateReport as QlibFactorEvaluateReport,
)
from src.contracts.schemas.qlib import (
    QlibFactorEvaluateRequest as QlibFactorEvaluateRequest,
)
from src.contracts.schemas.qlib import (
    QlibStatusResponse as QlibStatusResponse,
)
from src.contracts.schemas.qlib import (
    QlibTrainReport as QlibTrainReport,
)
from src.contracts.schemas.qlib import (
    QlibTrainRequest as QlibTrainRequest,
)
from src.contracts.schemas.quote import (
    QuoteIngestRequest as QuoteIngestRequest,
)
from src.contracts.schemas.reconciliation import (
    OrdersReconcileRequest as OrdersReconcileRequest,
)
from src.contracts.schemas.reconciliation import (
    SnapshotReconcileRequest as SnapshotReconcileRequest,
)
from src.contracts.schemas.reconciliation import (
    SnapshotReconcileResponse as SnapshotReconcileResponse,
)
from src.contracts.schemas.trading import (
    AlertExecuteRequest as AlertExecuteRequest,
)
from src.contracts.schemas.trading import (
    BacktestRunRequest as BacktestRunRequest,
)
from src.contracts.schemas.trading import (
    BatchOrderExecuteRequest as BatchOrderExecuteRequest,
)
from src.contracts.schemas.trading import (
    BatchOrderExecuteResponse as BatchOrderExecuteResponse,
)
from src.contracts.schemas.trading import (
    BatchOrderRegisterRequest as BatchOrderRegisterRequest,
)
from src.contracts.schemas.trading import (
    FeasibilityReportResponse as FeasibilityReportResponse,
)
from src.contracts.schemas.trading import (
    FeasibilityRequest as FeasibilityRequest,
)
from src.contracts.schemas.trading import (
    FeasibilityRowResponse as FeasibilityRowResponse,
)
from src.contracts.schemas.trading import (
    KillSwitchRequest as KillSwitchRequest,
)
from src.contracts.schemas.trading import (
    KillSwitchStatusResponse as KillSwitchStatusResponse,
)
from src.contracts.schemas.trading import (
    LiveStepRequest as LiveStepRequest,
)
from src.contracts.schemas.trading import (
    LocalPaperOrderResponse as LocalPaperOrderResponse,
)
from src.contracts.schemas.trading import (
    PaperOrderRequest as PaperOrderRequest,
)
from src.contracts.schemas.trading import (
    PaperStepRequest as PaperStepRequest,
)
