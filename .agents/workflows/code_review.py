"""四维度并行 Code Review 动态工作流脚本（配合 `run_workflow` 使用）。

覆盖架构实现、后端基础与接口质量、后端服务质量与性能、前端质量与性能四个只读审查维度，
对照 `CLAUDE.md`、`agents/review/AGENT.md` 与 `agents/architecture/AGENT.md` 审查当前工作区改动。
"""

import json
from pathlib import Path

WORKSPACE_ROOT = str(Path(WORKSPACES[0]).resolve()) if WORKSPACES else str(Path.cwd().resolve())

FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "dimension": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
                    "category": {"type": "string", "enum": ["architecture", "quality", "performance"]},
                    "file": {"type": "string"},
                    "line": {"type": "string"},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["severity", "category", "file", "line", "title", "detail", "fix"],
            },
        },
        "residual_risks": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["dimension", "findings", "residual_risks", "summary"],
}

COMMON_RULES = """
重要约束：
1. 本次任务仅执行只读 Code Review，严禁修改任何文件！
2. 仅审查当前工作区改动（通过 `git status --short` 与 `git diff` 识别全部修改与新增未跟踪源码文件）。若对应审查范围无改动，直接返回空 `findings` 并在 `summary` 说明。
3. 务必先检查实际代码和行号，只报告经源码证实的真实问题，不要猜测或编造。
4. 每条 finding 必须给出精确的 `file`、`line`（如 `L45-L62`）、`severity`（`blocker` / `major` / `minor`）、`category`（`architecture` / `quality` / `performance`）、`title`、`detail` 和可执行的 `fix`。
5. 优先使用 `batch_search`（git diff / text / regex）和并行 `read_file` 批量读取文件，保持高效。
"""

REVIEWS = [
    {
        "role": "架构与分层审查",
        "dimension": "架构实现",
        "prompt": f"""你正在对 `{WORKSPACE_ROOT}` 的当前工作区改动执行【架构实现】维度 Code Review。
{COMMON_RULES}

请对照 `CLAUDE.md` 与 `agents/architecture/AGENT.md` 重点审查本次改动的全部 Python 文件与配置契约：
1. 分层依赖方向：`(contracts | configs) -> (repositories | clients) -> agents -> services -> runtime -> routers`，且 `repositories` 与 `clients` 互不依赖、`runtime` 不导入 `routers`、只有 `main.py` 可跨层装配。
2. 模块职责边界：纯数据在 `contracts`，配置在 `configs`，ORM/DB 在 `repositories`，外部 HTTP 在 `clients`，模型/LLM 编排在 `agents`，业务流程在 `services`，DI/生命周期在 `runtime`，HTTP 入口在 `routers`。检查是否存在业务逻辑泄漏到 routers、或持久化/HTTP 耦合越界、或在 routers 内直接构造独立账本/客户端。
3. Python 类组织规范（非豁免模块，豁免路径仅 `main.py`、`src/routers/**`、`tests/**`）：
   - 是否以类管理代码，无顶层业务函数；
   - 类成员顺序是否严格遵守：1) 私有类变量 `_name`/`__name` -> 2) 公开类变量 -> 3) 私有方法 `def _name`/`def __name` -> 4) 公开方法 `def name`。
4. 配置与契约：`.env.example` 是否按统一 `# ===...===` 功能块清晰分节并与 `src/configs/` 字段及默认值对齐。

完成后调用 `finish` 返回结构化结果。""",
    },
    {
        "role": "后端基础与接口质量审查",
        "dimension": "代码质量（基础层与接口层）",
        "prompt": f"""你正在对 `{WORKSPACE_ROOT}` 的当前工作区改动执行【代码质量】维度 Code Review（范围：contracts, configs, repositories, clients, agents, runtime, routers, main.py, tests）。
{COMMON_RULES}

请对照 `agents/review/AGENT.md` 与 `CLAUDE.md` 重点审查该范围内所有修改与新增文件：
1. 正确性与边界条件：空值/None、时区/PIT 时间过滤、数值精度与非有限浮点（`inf`/`nan`）、状态机转换、并发竞态、路由参数校验与 HTTP 状态码。
2. 异常处理：是否吞掉异常、错误类型是否合理、事务/连接/锁是否正确释放。
3. 安全与凭据脱敏：Cookie、Token、API Key、京东登录态/二维码/授权凭证、用户敏感信息是否会泄露到日志、异常消息、API 响应或前端状态中；SQL 拼接是否安全（防注入）。
4. 中文注释与文档字符串规范：新增/修改的 Python 代码注释和 docstring 是否使用中文，是否误含计划文档条款编号（如 `§1.0`、`G-1`、`G-2` 等）。
5. 改动收敛度与测试覆盖：是否存在无关改动或遗漏的关键边界测试。

完成后调用 `finish` 返回结构化结果。""",
    },
    {
        "role": "后端服务质量与性能审查",
        "dimension": "代码质量与性能（services 与数据库层）",
        "prompt": f"""你正在对 `{WORKSPACE_ROOT}` 的当前工作区改动执行【代码质量 + 性能】维度 Code Review（范围：全部修改与新增的 `src/services/**` 与 `src/repositories/**`）。
{COMMON_RULES}

请重点审查该范围内所有修改与新增文件：
1. **代码质量**：业务正确性、PIT 防前视偏差、资金/克重/成本核算边界、时区一致性（UTC 与北京时间）、异常处理、中文注释规范（不得含 `§x.x` 或 `G-x` 编号）。
2. **异步与事件循环阻塞**：`async def` 路径中是否存在同步 SQLite 读写、同步文件 I/O、同步网络调用、或重 CPU 计算直接阻塞事件循环（未用 `asyncio.to_thread` 或合适异步封装）。
3. **SQLite 性能与锁竞争**：是否统一使用 `BEGIN IMMEDIATE`（避免 WAL 下 `DEFERRED` 升级写锁绕过 `busy_timeout`）、N+1 查询、循环内逐条 commit、缺少必要索引、长事务占用写锁、全表扫描或无上限查询。
4. **重复请求 / 轮询冗余 / 内存边界**：是否存在重复拉取外部接口、后台任务重叠执行、取消保护不完整、无上限增长的列表/字典/缓存或泄漏句柄。

完成后调用 `finish` 返回结构化结果。""",
    },
    {
        "role": "前端代码质量与性能审查",
        "dimension": "前端质量与性能（web/）",
        "prompt": f"""你正在对 `{WORKSPACE_ROOT}/web` 的当前工作区改动执行【代码质量 + 性能】维度 Code Review。
{COMMON_RULES}

请重点审查 `web/` 下所有修改与新增文件（如无 `web/` 改动则直接返回空 findings）：
1. **正确性与边界条件**：API 契约与后端 schema 是否一致、表单校验（如 `min`/`step`/日期解析）、空态/加载态/错误态处理、竞态条件（快速切换 tab/筛选/轮询返回乱序覆盖）、定时器与 `AbortController` 清理。
2. **安全与隐私**：是否存在 `dangerouslySetInnerHTML`、凭据/Cookie 不当暴露或持久化、未校验协议的外部链接跳转（`window.open` / `<a href>`）。
3. **轮询冗余与网络请求**：是否存在重复轮询、页面不可见时继续高频轮询、静态接口重复拉取或请求风暴。
4. **React 重渲染与内存边界**：`useEffect` 依赖循环或不稳定引用、单轮轮询多次分散 `setState` 导致整树重渲染、大列表无分页/虚拟化渲染、未清理的定时器或事件监听器。
5. **测试覆盖**：`web/src/__tests__/` 是否覆盖关键新增交互与边界。

完成后调用 `finish` 返回结构化结果。""",
    },
]

phase("Parallel Code Review")
results = await parallel(
    REVIEWS,
    lambda item: agent(
        item["prompt"],
        schema=FINDING_SCHEMA,
        role=item["role"],
        type_name="self",
        workspace="inherit",
    ),
)

phase("Summary")
for r in results:
    counts = {"blocker": 0, "major": 0, "minor": 0}
    for f in r["findings"]:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    log(
        f"[{r['dimension']}] blocker={counts['blocker']} "
        f"major={counts['major']} minor={counts['minor']} | {r['summary']}"
    )

print(json.dumps(results, ensure_ascii=False, indent=2))
