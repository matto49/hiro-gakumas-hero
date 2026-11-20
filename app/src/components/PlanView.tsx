// 编成方案的展示：支援卡 5 + 租借 1、回忆 4，带理由。编成卡和编成页共用。
import type { PlanSummary } from "../lib/plan";
import { ATTR_STYLE, TIMING_LABEL } from "../lib/labels";
import { cx } from "./ui";

export function PlanView({ plan, compact }: { plan: PlanSummary; compact?: boolean }) {
  const sup = plan.support;
  const mem = plan.memory;
  return (
    <div className="flex flex-col gap-2.5">
      <div className="text-xs text-ink-2">
        <b className="jp text-ink">{plan.idol.name ?? plan.idol.id ?? "?"}</b>
        {` · 主属性 ${plan.main_attr ?? "?"}`}
        {sup.set ? ` · 支援卡编成 ${sup.set}` : ""}
        {mem.set ? ` · 回忆编成 ${mem.set}` : ""}
      </div>
      <div>
        <div className="mb-1 text-[11px] text-ink-3">支援卡</div>
        {sup.error ? (
          <div className="text-xs text-err">{sup.error}</div>
        ) : (
          <div className="flex flex-col gap-1">
            {sup.own.map((o, i) => (
              <div key={i} className="flex items-center gap-2 rounded-lg border border-line bg-white px-2 py-1">
                <span className={cx("rounded px-1.5 text-[11px] font-bold", ATTR_STYLE[String(o.attr)] ?? "bg-chip text-ink-2")}>{o.attr ?? "?"}</span>
                <span className="jp min-w-0 flex-1 truncate">{o.name ?? "?"}</span>
                <span className="text-[11px] text-ink-3">Lv{o.level ?? "?"}</span>
                {!compact && <span className="max-w-[45%] truncate text-[11px] text-ink-3">{o.why.join("；")}</span>}
              </div>
            ))}
            {sup.rental && (
              <div className="flex items-center gap-2 rounded-lg border border-dashed border-line px-2 py-1 text-ink-2">
                <span className={cx("rounded px-1.5 text-[11px] font-bold", ATTR_STYLE[String(sup.rental.attr)] ?? "bg-chip text-ink-2")}>{sup.rental.attr ?? "?"}</span>
                <span className="jp min-w-0 flex-1 truncate">租借：{sup.rental.name ?? "开局时按实际列表挑"}</span>
                {sup.rental.estimated && <span className="text-[11px] text-ink-3">估算</span>}
              </div>
            )}
          </div>
        )}
      </div>
      <div>
        <div className="mb-1 text-[11px] text-ink-3">回忆</div>
        {mem.error ? (
          <div className="text-xs text-err">{mem.error}</div>
        ) : (
          <div className="flex flex-col gap-1">
            {mem.chosen.map((c, i) => (
              <div key={i} className="flex items-center gap-2 rounded-lg border border-line bg-white px-2 py-1">
                <span className="rounded bg-chip px-1.5 text-[11px] text-ink-2">{TIMING_LABEL[String(c.timing)] ?? "?"}</span>
                <span className="jp min-w-0 flex-1 truncate">
                  {c.name ?? "?"}
                  {"+".repeat(c.upgrade || 0)}
                </span>
                {!compact && c.role && <span className="max-w-[45%] truncate text-[11px] text-ink-3">{c.role}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
      {!compact && plan.notes.length > 0 && (
        <ul className="m-0 pl-4 text-[11.5px] text-ink-2">
          {plan.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
