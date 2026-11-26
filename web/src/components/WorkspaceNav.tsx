import { Activity, Compass, FlaskConical, Globe2, Maximize, Newspaper, UserRound, Wallet } from "lucide-react";

export type WorkspaceTab = "overview" | "markets" | "news" | "portfolio" | "research" | "accounts";

const NAV_ITEMS = [
  { id: "overview", label: "总览", icon: Compass },
  { id: "markets", label: "行情", icon: Globe2 },
  { id: "news", label: "资讯", icon: Newspaper },
  { id: "portfolio", label: "账本", icon: Wallet },
  { id: "research", label: "研究", icon: FlaskConical },
  { id: "accounts", label: "账号", icon: UserRound },
] as const;

interface WorkspaceNavProps {
  activeTab: WorkspaceTab;
  onChangeTab: (tab: WorkspaceTab) => void;
  pending: number;
  newsFlags: number;
}

export function WorkspaceNav({ activeTab, onChangeTab, pending, newsFlags }: WorkspaceNavProps) {
  return (
    <div className="workspace-width flex h-17 items-center justify-between gap-3">
      <button type="button" onClick={() => onChangeTab("overview")} aria-label="GoldQuant 首页"
        className="inline-flex min-h-10 items-center gap-3 rounded-md text-xl font-semibold tracking-tight text-white">
        <span className="brand-mark size-9"><Activity className="size-4.5" /></span>
        GoldQuant
      </button>
      <nav className="workspace-nav md:ml-auto" aria-label="工作台导航">
        {NAV_ITEMS.map(({ id, label, icon: Icon }) => {
          const count = id === "overview" ? pending : id === "news" ? newsFlags : 0;
          return (
            <button key={id} type="button" aria-current={activeTab === id ? "page" : undefined}
              onClick={() => onChangeTab(id)} className="workspace-tab">
              <Icon className="size-4 md:hidden" aria-hidden="true" />
              <span>{label}</span>
              {count > 0 && <span className="nav-count">{count}</span>}
            </button>
          );
        })}
      </nav>
      <a href="/?view=screen" className="secondary-button min-h-9 shrink-0 px-2.5 text-xs">
        <Maximize className="size-3.5" /> 实时大屏
      </a>
    </div>
  );
}
