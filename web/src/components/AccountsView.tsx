import {
  Check,
  CheckCircle2,
  Clock,
  Copy,
  ExternalLink,
  LogIn,
  LogOut,
  RefreshCw,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { JDAuthStatusResponse, JDHoldingsResponse } from "../api/types";
import { formatBeijingTime } from "../quotes";

function sanitizeAuthorizeUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  try {
    const parsed = new URL(raw);
    return parsed.protocol === "https:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

interface AccountsViewProps {
  authStatus: JDAuthStatusResponse | null;
  authFailed: boolean;
  jdHoldings: JDHoldingsResponse | null;
  jdHoldingsMessage: string | null;
  holdingsConfirmedAt: string | null;
  busyAction: string | null;
  refreshing: boolean;
  onRefresh: () => void;
  onStartLogin: () => Promise<string | null>;
  onCompleteLogin: () => Promise<boolean>;
  onRenewAuth?: () => Promise<boolean>;
  onVerifyAuth: () => Promise<void>;
  onLogout: () => Promise<boolean>;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "长期有效";
  if (seconds <= 0) return "已过期";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分钟`;
  if (minutes > 0) return `${minutes} 分钟 ${secs} 秒`;
  return `${secs} 秒`;
}

export function AccountsView({
  authStatus,
  authFailed,
  jdHoldings,
  jdHoldingsMessage,
  holdingsConfirmedAt,
  busyAction,
  refreshing,
  onRefresh,
  onStartLogin,
  onCompleteLogin,
  onRenewAuth,
  onVerifyAuth,
  onLogout,
}: AccountsViewProps) {
  const [latestAuthorizeUrl, setLatestAuthorizeUrl] = useState<string | null>(null);
  const latestAuthorizeBaselineRef = useRef<JDAuthStatusResponse | null>(null);
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<number | null>(null);

  const loggedIn = authStatus?.logged_in === true;
  const reauthRecommended = authStatus?.reauth_recommended === true;
  const leadMinutes = Math.max(1, Math.round((authStatus?.reauth_lead_seconds ?? 1800) / 60));
  const autoRenewEnabled = authStatus?.auto_renew_enabled === true;
  const autoRenewInProgress = authStatus?.auto_renew_in_progress === true || busyAction === "jd-renew";
  const lastAutoRenewStatus = authStatus?.last_auto_renew_status ?? "idle";
  const pendingLogin = authStatus?.pending_login === true;

  useEffect(() => {
    if (
      authStatus !== latestAuthorizeBaselineRef.current &&
      authStatus &&
      !authStatus.pending_login
    ) {
      latestAuthorizeBaselineRef.current = null;
      setLatestAuthorizeUrl(null);
    }
  }, [authStatus]);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
    };
  }, []);

  const hasFreshFallback =
    latestAuthorizeUrl !== null && authStatus === latestAuthorizeBaselineRef.current;
  const authorizeUrl = pendingLogin
    ? sanitizeAuthorizeUrl(authStatus?.authorize_url ?? latestAuthorizeUrl)
    : hasFreshFallback || !authStatus
      ? sanitizeAuthorizeUrl(latestAuthorizeUrl)
      : null;
  const hasPendingSession = pendingLogin || Boolean(authorizeUrl);

  const statusBadge = loggedIn
    ? reauthRecommended
      ? { label: "即将到期 · 建议重新授权", cls: "border-amber-400/40 bg-amber-400/12 text-amber-200" }
      : { label: "已登录", cls: "border-emerald-400/40 bg-emerald-400/12 text-emerald-200" }
    : authStatus
      ? { label: "未登录", cls: "border-slate-400/35 bg-slate-400/10 text-slate-300" }
      : { label: authFailed ? "状态读取失败" : "读取中", cls: "border-slate-400/35 bg-slate-400/10 text-slate-300" };

  const handleStartLogin = async () => {
    const url = sanitizeAuthorizeUrl(await onStartLogin());
    if (url) {
      latestAuthorizeBaselineRef.current = authStatus;
      setLatestAuthorizeUrl(url);
      window.open(url, "_blank", "noopener,noreferrer");
    }
  };

  const handleCompleteLogin = async () => {
    const ok = await onCompleteLogin();
    if (ok) {
      latestAuthorizeBaselineRef.current = null;
      setLatestAuthorizeUrl(null);
    }
  };

  const handleLogout = async () => {
    if (!window.confirm("退出后将清除本地保存的京东授权凭据，实盘持仓与对账将暂停直至重新登录。确认退出？")) {
      return;
    }
    const ok = await onLogout();
    if (ok) {
      latestAuthorizeBaselineRef.current = null;
      setLatestAuthorizeUrl(null);
    }
  };

  const handleCopyUrl = async () => {
    if (!authorizeUrl) return;
    try {
      await navigator.clipboard.writeText(authorizeUrl);
      setCopied(true);
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
      copyTimerRef.current = window.setTimeout(() => {
        setCopied(false);
        copyTimerRef.current = null;
      }, 2000);
    } catch {
      // 浏览器禁用剪贴板时保持静默，用户仍可直接点击下方链接
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <section className="surface holdings-surface p-5 sm:p-7" aria-labelledby="jd-account-title">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b border-white/8 pb-5">
          <div className="flex items-start gap-3.5">
            <span className="brand-mark size-11" aria-hidden="true">
              <UserRound className="size-5" />
            </span>
            <div>
              <div className="flex flex-wrap items-center gap-2.5">
                <h2 id="jd-account-title" className="section-heading">京东黄金账号</h2>
                <span className={`data-label ${statusBadge.cls}`}>{statusBadge.label}</span>
              </div>
              <p className="mt-1 text-sm text-slate-400">
                授权后用于同步京东黄金真实持仓、历史订单、收益日历与对账核验，登录态与本地 CLI 共享。
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onRefresh}
              disabled={refreshing}
              className="secondary-button min-h-9 px-3 text-xs"
            >
              <RefreshCw className={`size-3.5 ${refreshing ? "animate-spin" : ""}`} />
              刷新状态
            </button>
            {onRenewAuth && (
              <button
                type="button"
                onClick={() => void onRenewAuth()}
                disabled={busyAction !== null || autoRenewInProgress}
                className="secondary-button min-h-9 border-[#f0d5a0]/35 bg-[#f0d5a0]/10 px-3 text-xs text-[#f6deb0] hover:bg-[#f0d5a0]/18"
              >
                <RefreshCw className={`size-3.5 ${autoRenewInProgress ? "animate-spin" : ""}`} />
                {autoRenewInProgress ? "自动续约中…" : "立即自动续约"}
              </button>
            )}
            <button
              type="button"
              onClick={() => void onVerifyAuth()}
              disabled={!loggedIn || busyAction !== null}
              className="secondary-button min-h-9 px-3 text-xs"
            >
              <ShieldCheck className="size-3.5" />
              {busyAction === "jd-verify" ? "验证中…" : "验证权限"}
            </button>
            {loggedIn && (
              <button
                type="button"
                onClick={() => void handleLogout()}
                disabled={busyAction !== null}
                className="secondary-button min-h-9 border-rose-400/30 bg-rose-400/8 px-3 text-xs text-rose-200 hover:bg-rose-400/15"
              >
                <LogOut className="size-3.5" />
                {busyAction === "jd-logout" ? "退出中…" : "退出登录"}
              </button>
            )}
          </div>
        </div>

        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          <div className="metric-panel p-4">
            <span className="metric-label">剩余可用时长</span>
            <p className="mt-1.5 text-lg font-semibold text-white">
              {loggedIn ? formatDuration(authStatus?.usable_seconds) : "未登录"}
            </p>
            <p className="mt-1 text-xs text-slate-400">
              {autoRenewEnabled
                ? `已开启到期前 ${leadMinutes} 分钟浏览器自动续约`
                : authStatus?.refresh_supported
                  ? "支持静默续期"
                  : "未开启浏览器自动续约"}
            </p>
          </div>

          <div className="metric-panel p-4">
            <span className="metric-label">令牌到期时间（北京时间）</span>
            <p className="mt-1.5 text-lg font-semibold text-white">
              {loggedIn && authStatus?.expires_at ? formatBeijingTime(authStatus.expires_at) : "—"}
            </p>
            <p className="mt-1 text-xs text-slate-400">
              {reauthRecommended
                ? `已进入到期前 ${leadMinutes} 分钟自动续约窗口`
                : `到期前 ${leadMinutes} 分钟自动浏览器续约`}
            </p>
          </div>

          <div className="metric-panel p-4">
            <span className="metric-label">京东持仓同步</span>
            <p className="mt-1.5 text-lg font-semibold text-[#f6deb0]">
              {jdHoldings ? `${jdHoldings.totalGramAll.toFixed(4)} g` : (jdHoldingsMessage ?? "暂无本地持仓")}
            </p>
            <p className="mt-1 text-xs text-slate-400">
              {holdingsConfirmedAt ? `最近确认 ${formatBeijingTime(holdingsConfirmedAt)}` : "登录完成后将自动尝试同步"}
            </p>
          </div>
        </div>

        {(autoRenewInProgress || lastAutoRenewStatus === "failed" || lastAutoRenewStatus === "succeeded") && (
          <div
            className={`mt-4 rounded-xl border px-4 py-3 text-xs ${
              lastAutoRenewStatus === "failed" && !autoRenewInProgress
                ? "border-amber-400/35 bg-amber-400/10 text-amber-100"
                : autoRenewInProgress
                  ? "border-sky-400/35 bg-sky-400/10 text-sky-100"
                  : "border-emerald-400/30 bg-emerald-400/8 text-emerald-100"
            }`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium">
                {autoRenewInProgress
                  ? "正在通过浏览器自动化执行京东账号续约…"
                  : lastAutoRenewStatus === "failed"
                    ? `最近一次浏览器自动续约失败：${authStatus?.last_auto_renew_error ?? "请检查 Chrome CDP 或改为人工授权"}`
                    : "最近一次浏览器自动续约已成功完成"}
              </span>
              {authStatus?.last_auto_renew_at && (
                <span className="text-[11px] opacity-80">
                  {formatBeijingTime(authStatus.last_auto_renew_at)}
                </span>
              )}
            </div>
          </div>
        )}
      </section>

      <section className="surface p-5 sm:p-7" aria-labelledby="jd-login-flow-title">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 id="jd-login-flow-title" className="section-heading">京东浏览器授权登录</h2>
            <p className="mt-1 text-sm text-slate-400">
              采用京东官方 PKCE 浏览器授权流程；自动续约仅在本地通过受保护环境变量读取凭据，绝不经由接口暴露。
            </p>
          </div>
          {pendingLogin && authStatus?.pending_login_expires_at && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-400/30 bg-amber-400/10 px-3 py-1 text-xs text-amber-200">
              <Clock className="size-3.5" />
              待确认会话有效至 {formatBeijingTime(authStatus.pending_login_expires_at)}
            </span>
          )}
        </div>

        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <div className="metric-panel flex flex-col justify-between gap-4 p-4 sm:p-5">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-white">
                <span className="inline-flex size-6 items-center justify-center rounded-full bg-[#f0d5a0]/18 text-xs text-[#f0d5a0]">
                  1
                </span>
                发起京东授权并打开登录页
              </div>
              <p className="mt-2 text-xs leading-relaxed text-slate-400">
                点击下方按钮获取京东官方授权链接并在新标签页打开，使用京东 App 扫码或登录并同意授权。
              </p>
            </div>
            <div>
              <button
                type="button"
                onClick={() => void handleStartLogin()}
                disabled={busyAction !== null}
                className="primary-button w-full sm:w-auto"
              >
                <LogIn className="size-4" />
                {busyAction === "jd-login"
                  ? "正在生成授权链接…"
                  : loggedIn
                    ? "重新登录 / 切换京东账号"
                    : "登录京东"}
              </button>
            </div>
          </div>

          <div className="metric-panel flex flex-col justify-between gap-4 p-4 sm:p-5">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-white">
                <span className="inline-flex size-6 items-center justify-center rounded-full bg-[#f0d5a0]/18 text-xs text-[#f0d5a0]">
                  2
                </span>
                确认已完成授权并保存登录态
              </div>
              <p className="mt-2 text-xs leading-relaxed text-slate-400">
                在京东页面看到授权成功提示后，返回此处点击确认按钮，系统将换取访问令牌并立即同步持仓。
              </p>
            </div>
            <div>
              <button
                type="button"
                onClick={() => void handleCompleteLogin()}
                disabled={!hasPendingSession || busyAction !== null}
                className="secondary-button w-full border-emerald-400/35 bg-emerald-400/10 text-emerald-100 hover:bg-emerald-400/18 sm:w-auto"
              >
                <CheckCircle2 className="size-4" />
                {busyAction === "jd-complete" ? "正在同步登录态…" : "我已完成授权"}
              </button>
            </div>
          </div>
        </div>

        {authorizeUrl && (
          <div className="mt-4 rounded-xl border border-[#f0d5a0]/30 bg-[#f0d5a0]/8 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium text-[#f0d5a0]">当前待确认的京东授权链接</p>
                <p className="mt-1 truncate font-mono text-xs text-slate-300" title={authorizeUrl}>
                  {authorizeUrl}
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <a
                  href={authorizeUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="primary-button min-h-9 px-3 text-xs"
                >
                  <ExternalLink className="size-3.5" />
                  打开京东授权页
                </a>
                <button
                  type="button"
                  onClick={() => void handleCopyUrl()}
                  className="secondary-button min-h-9 px-3 text-xs"
                >
                  {copied ? <Check className="size-3.5 text-emerald-300" /> : <Copy className="size-3.5" />}
                  {copied ? "已复制" : "复制链接"}
                </button>
              </div>
            </div>
            <p className="mt-2 text-xs text-slate-400">
              如果浏览器拦截了自动弹窗，请点击“打开京东授权页”或复制链接后在浏览器中打开；完成授权后点击上方“我已完成授权”。
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
