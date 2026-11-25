import { ChartNoAxesCombined, CheckCircle2, FlaskConical, Newspaper, Wallet, X, XCircle } from "lucide-react";
import { lazy, Suspense, useCallback, useState } from "react";
import { api } from "./api/client";
import type { BacktestSummary, Evaluation, TrainingPipelineReport } from "./api/types";
import { QUERY_LABELS, useWorkspaceData } from "./api/useWorkspaceData";
import { MarketsView } from "./components/MarketsView";
import { NewsView } from "./components/NewsView";
import { OverviewView } from "./components/OverviewView";
import { PortfolioView } from "./components/PortfolioView";
import type { WorkspaceTab } from "./components/WorkspaceNav";
import { TopStatusBar } from "./components/TopStatusBar";

const LiveScreen = lazy(() => import("./components/LiveScreen"));
const ResearchView = lazy(async () => ({ default: (await import("./components/ResearchView")).ResearchView }));

const PAGE_META = {
  markets: { title: "行情", icon: ChartNoAxesCombined },
  news: { title: "资讯", icon: Newspaper },
  portfolio: { title: "账本", icon: Wallet },
  research: { title: "研究", icon: FlaskConical },
};

interface ToastItem {
  id: number;
  type: "ok" | "error";
  text: string;
}

export default function App() {
  const screenOnly = new URLSearchParams(window.location.search).get("view") === "screen";
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("overview");
  const [backtestResult, setBacktestResult] = useState<BacktestSummary | null>(null);
  const [trainReport, setTrainReport] = useState<TrainingPipelineReport | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [killBusy, setKillBusy] = useState(false);
  const [focusAlertId, setFocusAlertId] = useState<string | null>(null);
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const { queries, jdHoldings, jdHoldingsError, lastUpdated, refreshing, refresh, rememberAlert } = useWorkspaceData(
    autoRefresh,
    screenOnly,
    activeTab === "portfolio"
  );
  const liveStatus = queries.live?.data ?? null;
  const modelStatus = queries.models?.data ?? null;
  const newsOverview = queries.news?.data ?? null;
  const products = queries.products?.data ?? [];
  const quoteStatuses = queries.quotes?.data ?? [];
  const alerts = queries.alerts?.data ?? [];
  const orders = queries.orders?.data ?? [];
  const reconReports = queries.reconciliation?.data ?? [];
  const failedQueries = (Object.keys(QUERY_LABELS) as (keyof typeof QUERY_LABELS)[])
    .filter((key) => queries[key]?.failed);
  const previousDataFailed = failedQueries.some((key) => queries[key]?.updatedAt);
  const quotesFailed = !!queries.quotes?.failed;

  const notify = useCallback((type: "ok" | "error", text: string) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev.slice(-2), { id, type, text }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((item) => item.id !== id));
    }, 5000);
  }, []);

  const runAction = async (key: string, fn: () => Promise<string>) => {
    setBusyAction(key);
    try {
      const message = await fn();
      notify("ok", message);
      await refresh();
    } catch (err) {
      notify("error", err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusyAction(null);
    }
  };

  const navigate = (tab: WorkspaceTab) => {
    if (tab === activeTab) return;
    setActiveTab(tab);
    window.scrollTo(0, 0);
  };

  const evaluateLive = () => {
    navigate("overview");
    void runAction("live-step", async () => {
      try {
        const result = await api.runLiveStep();
        if (result.alert) {
          rememberAlert(result.alert);
          setFocusAlertId(result.alert.alert_id);
        }
        setEvaluation({ at: result.as_of, result });
        return result.message;
      } catch (error) {
        setEvaluation({
          at: new Date().toISOString(),
          error: error instanceof Error ? error.message : "评估请求失败",
        });
        throw error;
      }
    });
  };

  const changeKillSwitch = async () => {
    if (killBusy) return;
    const resume = !queries.live?.failed && liveStatus?.kill_switch_active;
    if (resume && !window.confirm("解除急停会将实盘风控重置为正常。请先复核当前风控与对账结果。确认解除急停？")) return;
    setKillBusy(true);
    try {
      const result = await (resume ? api.resumeLive() : api.haltLive());
      notify("ok", result.message);
    } catch (error) {
      notify("error", error instanceof Error ? error.message : "急停操作失败");
    } finally {
      await refresh();
      setKillBusy(false);
    }
  };

  const PageIcon = activeTab === "overview" ? null : PAGE_META[activeTab].icon;

  if (screenOnly) {
    return (
      <Suspense fallback={<p role="status" className="p-6 text-slate-300">正在打开实时大屏…</p>}>
        <LiveScreen
          quotes={quoteStatuses}
          products={products}
          liveStatus={liveStatus}
          quotesUpdatedAt={queries.quotes?.updatedAt}
          quotesFailed={quotesFailed}
          liveFailed={!!queries.live?.failed}
          refreshing={refreshing}
          onRefresh={() => void refresh()}
        />
      </Suspense>
    );
  }

  return (
    <div className="min-h-screen">
      <TopStatusBar
        activeTab={activeTab}
        onChangeTab={navigate}
        liveStatus={liveStatus}
        liveFailed={!!queries.live?.failed}
        liveUpdatedAt={queries.live?.updatedAt}
        autoRefresh={autoRefresh}
        lastUpdated={lastUpdated}
        refreshing={refreshing}
        killBusy={killBusy}
        newsFlags={newsOverview?.active_risk_flags.length ?? 0}
        onToggleAutoRefresh={() => setAutoRefresh((value) => !value)}
        onRefresh={() => void refresh()}
        onToggleKillSwitch={() => void changeKillSwitch()}
      />

      <main className="workspace-width pt-5 pb-22 md:pt-8 md:pb-12">
        {activeTab !== "overview" && PageIcon && (
          <header className="page-title">
            <span aria-hidden="true"><PageIcon className="size-5 sm:size-6" /></span>
            <h1>{PAGE_META[activeTab].title}</h1>
          </header>
        )}
        {failedQueries.length > 0 && (
          <p role="status" className="mb-4 rounded-lg border border-amber-400/25 bg-amber-400/8 px-3 py-2 text-sm text-amber-100">
            更新失败 · {failedQueries.map((key) => QUERY_LABELS[key]).join("、")}
            {previousDataFailed && "。有缓存的项目显示上次成功的数据。"}
          </p>
        )}
        {activeTab === "overview" && (
          <OverviewView
            liveStatus={liveStatus}
            jdHoldings={jdHoldings}
            jdHoldingsError={jdHoldingsError}
            modelStatus={modelStatus}
            newsOverview={newsOverview}
            products={products}
            quoteStatuses={quoteStatuses}
            alerts={alerts}
            alertsLoaded={!!queries.alerts?.updatedAt}
            alertsFailed={!!queries.alerts?.failed}
            liveFailed={!!queries.live?.failed}
            quotesFailed={quotesFailed}
            evaluation={evaluation}
            focusAlertId={focusAlertId}
            onAlertFocused={() => setFocusAlertId(null)}
            onEvaluateLive={evaluateLive}
            busyAction={killBusy ? "kill-switch" : busyAction}
            onNavigate={navigate}
            onConfirmAlert={(id) =>
              void runAction(`confirm-${id}`, async () => {
                await api.confirmAlert(id);
                return `已确认提醒 ${id} 的交易意向`;
              })
            }
            onDeclineAlert={(id) =>
              void runAction(`decline-${id}`, async () => {
                await api.declineAlert(id);
                return `已拒绝提醒 ${id}`;
              })
            }
            onExecuteAlert={async (id, payload) => {
              await runAction(`exec-${id}`, async () => {
                await api.executeAlert(id, payload);
                return `已登记提醒 ${id} 成交（${payload.filled_grams}g @ ¥${payload.filled_price}）`;
              });
            }}
            onReconcileJd={() =>
              void runAction("reconcile-jd", async () => {
                const res = await api.reconcileJdHoldings();
                return res.report.summary;
              })
            }
            onStepPaper={() =>
              void runAction("paper-step", async () => {
                const res = await api.stepPaper();
                return res.message;
              })
            }
            onSyncNews={() =>
              void runAction("news-sync", async () => {
                const res = await api.syncNewsFlash(10);
                return `资讯同步完成：拉取 ${res.fetched_count} 条，新增 ${res.ingested_count} 条，跳过 ${res.skipped_count} 条`;
              })
            }
          />
        )}

        {activeTab === "markets" && (
          <MarketsView
            products={products}
            statuses={quoteStatuses}
            loading={!queries.products || !queries.quotes}
            quotesFailed={quotesFailed}
            busyAction={busyAction}
            onCollectAll={() =>
              void runAction("collect-all", async () => {
                const list = await api.collectAllQuotes();
                const failed = list.filter((s) => s.last_error).length;
                return `全量行情采集完成：${list.length} 个品种${failed > 0 ? `（${failed} 个失败）` : ""}`;
              })
            }
          />
        )}

        {activeTab === "news" && (
          <NewsView
            newsOverview={newsOverview}
            modelStatus={modelStatus}
            busyAction={busyAction}
            onSyncNews={() =>
              void runAction("news-sync", async () => {
                const res = await api.syncNewsFlash(10);
                return `资讯同步完成：拉取 ${res.fetched_count} 条，新增 ${res.ingested_count} 条，跳过 ${res.skipped_count} 条`;
              })
            }
            onRegisterMacro={async (payload) => {
              await runAction("macro-add", async () => {
                await api.registerMacroEvent(payload);
                return `已登记宏观日程：${payload.event_name}`;
              });
            }}
          />
        )}

        {activeTab === "portfolio" && (
          <PortfolioView
            liveStatus={liveStatus}
            jdHoldings={jdHoldings}
            jdHoldingsError={jdHoldingsError}
            referenceGoldPrice={quoteStatuses.find((s) => s.product_code === "CMBC-JCJ")?.latest?.price ?? null}
            orders={orders}
            reconReports={reconReports}
            busyAction={busyAction}
            liveFailed={!!queries.live?.failed}
            onReconcileJd={() =>
              void runAction("reconcile-jd", async () => {
                const res = await api.reconcileJdHoldings();
                return res.report.summary;
              })
            }
            onInitLedgerFromJd={() => {
              const configuredCash = liveStatus?.default_initial_cash ?? 100000;
              if (
                !window.confirm(
                  `将清空当前本地影子账本流水，按京东真实黄金历史订单流水与持仓重放初始化（初始资金 ¥${configuredCash.toLocaleString("zh-CN")}）。确认继续？`
                )
              ) {
                return;
              }
              void runAction("init-ledger-jd", async () => {
                const snap = await api.initLedgerFromJd();
                return `影子账本已按京东持仓初始化：持仓 ${snap.gold_total_g.toFixed(4)}g，可用现金 ¥${snap.cash_available.toFixed(2)}`;
              });
            }}
            onSubmitPaperOrder={(direction, grams) =>
              void runAction("paper-order", async () => {
                await api.submitLocalPaperOrder({ direction, grams });
                return `本地模拟盘已提交 ${direction === "BUY" ? "买入" : "卖出"} ${grams}g`;
              })
            }
          />
        )}

        {activeTab === "research" && (
          <Suspense fallback={<p role="status" className="text-slate-300">正在加载研究页面…</p>}>
            <ResearchView
              busyAction={busyAction}
              backtestResult={backtestResult}
              trainReport={trainReport}
              onRunBacktest={(params) =>
                void runAction("backtest", async () => {
                  const summary = await api.runBacktest(params);
                  setBacktestResult(summary);
                  return `回测完成：${summary.quote_count} 条行情，总收益 ${(summary.shadow_report.total_return_pct * 100).toFixed(2)}%`;
                })
              }
              onRunTrain={(params) =>
                void runAction("train", async () => {
                  const report = await api.runTrainingPipeline(params);
                  setTrainReport(report);
                  const total = Object.values(report.sample_counts).reduce((a, b) => a + b, 0);
                  return `样本评估完成：共构建 ${total} 条 PIT 样本`;
                })
              }
            />
          </Suspense>
        )}
      </main>

      {/* 浮动 Toast 通知栈（右下角，不挤占页面布局） */}
      {toasts.length > 0 && (
        <div className="fixed right-4 bottom-16 z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2 md:bottom-5">
          {toasts.map((toast) => (
            <div
              key={toast.id}
              role={toast.type === "error" ? "alert" : "status"}
              className={`flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-xs shadow-2xl backdrop-blur-md ${
                toast.type === "ok"
                  ? "border-emerald-500/35 bg-[#0f1714]/95 text-emerald-200"
                  : "border-rose-500/45 bg-[#1b1014]/95 text-rose-200"
              }`}
            >
              {toast.type === "ok" ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
              ) : (
                <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
              )}
              <span className="flex-1 leading-relaxed">{toast.text}</span>
              <button
                type="button"
                aria-label="关闭通知"
                onClick={() => setToasts((prev) => prev.filter((item) => item.id !== toast.id))}
                className="text-slate-500 hover:text-slate-200"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
