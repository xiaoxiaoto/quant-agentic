import { Link2 } from "lucide-react";
import { useEffect, useState } from "react";
import type { HoldingPeriod } from "../../api/types";
import { ALL_HOLDING_PERIODS } from "./types";
import { parseFiniteNumber } from "./validation";

export function HorizonMultiSelect({
  selected,
  onChange,
}: {
  selected: HoldingPeriod[];
  onChange: (next: HoldingPeriod[]) => void;
}) {
  const toggle = (period: HoldingPeriod) => {
    if (selected.includes(period)) {
      if (selected.length === 1) return;
      onChange(selected.filter((item) => item !== period));
    } else {
      onChange([...selected, period]);
    }
  };
  return (
    <div className="mt-1 flex flex-wrap gap-1.5">
      {ALL_HOLDING_PERIODS.map((period) => {
        const active = selected.includes(period);
        return (
          <button
            key={period}
            type="button"
            onClick={() => toggle(period)}
            className={`rounded-md border px-2 py-1 text-xs font-medium transition-colors ${
              active
                ? "border-amber-300/60 bg-amber-400/15 text-amber-200"
                : "border-white/10 bg-white/[0.03] text-slate-400 hover:text-slate-200"
            }`}
          >
            {period}
          </button>
        );
      })}
    </div>
  );
}

export function LinkedBadge({ value, label }: { value: string | null | undefined; label: string }) {
  if (!value) return null;
  return (
    <span
      title={value}
      className="mt-1 inline-flex max-w-full items-center gap-1 truncate rounded border border-cyan-400/30 bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-200"
    >
      <Link2 className="size-3 shrink-0" />
      <span className="truncate">
        {label}：{value}
      </span>
    </span>
  );
}

export function NumericInputField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  className = "block",
}: {
  label: string;
  value: number | undefined;
  onChange: (next: number | undefined) => void;
  min?: number;
  max?: number;
  step?: string | number;
  className?: string;
}) {
  const [draft, setDraft] = useState(value !== undefined && Number.isFinite(value) ? String(value) : "");

  useEffect(() => {
    const parsedDraft = parseFiniteNumber(draft);
    if (value !== parsedDraft) {
      setDraft(value !== undefined && Number.isFinite(value) ? String(value) : "");
    }
  }, [value, draft]);

  return (
    <label className={className}>
      <span className="text-slate-400">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={draft}
        onChange={(e) => {
          const raw = e.target.value;
          setDraft(raw);
          onChange(parseFiniteNumber(raw));
        }}
        className="field mt-1 w-full px-2.5 py-1.5"
      />
    </label>
  );
}
