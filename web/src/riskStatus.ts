export const RISK_STYLE: Record<string, { label: string; tone: string }> = {
  NORMAL: { label: "风控正常", tone: "text-emerald-300" },
  NO_NEW_RISK: { label: "禁止开仓", tone: "text-amber-200" },
  REDUCE_ONLY: { label: "仅限减仓", tone: "text-amber-200" },
  HALT_RECONCILE: { label: "对账熔断", tone: "text-rose-200" },
};

