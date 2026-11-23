// 编成方案的展示：支援卡 5 + 租借 1、回忆 4，带理由。编成卡和编成页共用。
import { useEffect, useState } from "react";
import { ATTR_ZH, TYPE_ZH, activeAbilities, loadSupports, spRate, useLookup, type Supports } from "../lib/catalog";
import { useHiro } from "../lib/hiro";
import type { PlanSummary } from "../lib/plan";
import { ATTR_STYLE, TIMING_LABEL } from "../lib/labels";
import { RowTip, Thumb, TipSection } from "./CatalogBits";
import { cx } from "./ui";

export function PlanView({ plan, compact }: { plan: PlanSummary; compact?: boolean }) {
  const sup = plan.support;
  const mem = plan.memory;
  // 资料库（本机）：中文名、小图、悬浮说明；没同步时照旧显示日文原名
  const call = useHiro();
  const [supports, setSupports] = useState<Supports | null>(null);
  useEffect(() => {
    loadSupports(call).then(setSupports, () => setSupports(null));
  }, [call]);
  const look = useLookup(call, [plan.idol.id ?? "", ...mem.chosen.map((c) => c.card_id ?? "")]);
  const idolZh = plan.idol.id ? look?.zh.items[plan.idol.id]?.name : null;
  return (
    <div className="flex flex-col gap-2.5">
      <div className="text-xs text-ink-2">
        <b className={cx("text-ink", !idolZh && "jp")}>{idolZh ?? plan.idol.name ?? plan.idol.id ?? "?"}</b>
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
            {sup.own.map((o, i) => {
              const info = o.id ? supports?.cards[o.id] : undefined;
              const abilities = info ? activeAbilities(info, o.level) : [];
              const sp = info ? spRate(info) : null;
              const tip = info && (
                <>
                  <div className="text-[13px] font-semibold text-ink">{info.name.zh ?? info.name.ja}</div>
                  <div className="jp text-[11.5px] text-ink-3">{info.name.ja}</div>
                  <div className="mt-1 text-[11.5px] text-ink-2">
                    {TYPE_ZH[info.type] ?? info.type} · {info.rarity} · Lv {o.level ?? "?"}
                  </div>
                  {sp && <TipSection title="SP 发生率">{sp}</TipSection>}
                  {abilities.length > 0 && (
                    <TipSection title={`现在的能力（Lv ${o.level ?? "?"}）`}>
                      {abilities.map((a, k) => (
                        <div key={k}>· {a}</div>
                      ))}
                    </TipSection>
                  )}
                </>
              );
              return (
                <RowTip key={i} content={tip || null}>
                  <div className="flex items-center gap-2 rounded-lg border border-line bg-white px-2 py-1">
                    {o.id && <Thumb asset={`support-${o.id}`} className="size-7" />}
                    <span className={cx("rounded px-1.5 text-[11px] font-bold", ATTR_STYLE[String(o.attr)] ?? "bg-chip text-ink-2")}>{ATTR_ZH[String(o.attr)] ?? o.attr ?? "?"}</span>
                    <span className="min-w-0 flex-1 truncate">
                      <span className={cx(!info?.name.zh && "jp")}>{info?.name.zh ?? o.name ?? "?"}</span>
                      {info?.name.zh && o.name && <span className="jp ml-1.5 text-[11px] text-ink-3">{o.name}</span>}
                    </span>
                    <span className="text-[11px] text-ink-3">Lv{o.level ?? "?"}</span>
                    {!compact && <span className="max-w-[45%] truncate text-[11px] text-ink-3">{o.why.join("；")}</span>}
                  </div>
                </RowTip>
              );
            })}
            {sup.rental &&
              (() => {
                const r = sup.rental;
                const zh = r.id ? supports?.cards[r.id]?.name.zh : undefined;
                return (
                  <div className="flex items-center gap-2 rounded-lg border border-dashed border-line px-2 py-1 text-ink-2">
                    {r.id && <Thumb asset={`support-${r.id}`} className="size-7" />}
                    <span className={cx("rounded px-1.5 text-[11px] font-bold", ATTR_STYLE[String(r.attr)] ?? "bg-chip text-ink-2")}>
                      {ATTR_ZH[String(r.attr)] ?? r.attr ?? "?"}
                    </span>
                    <span className="min-w-0 flex-1 truncate">
                      租借：
                      <span className={cx(!zh && "jp")}>{zh ?? r.name ?? "开局时按实际列表挑"}</span>
                      {zh && r.name && <span className="jp ml-1.5 text-[11px] text-ink-3">{r.name}</span>}
                    </span>
                    {r.estimated && <span className="text-[11px] text-ink-3">估算</span>}
                  </div>
                );
              })()}
          </div>
        )}
      </div>
      <div>
        <div className="mb-1 text-[11px] text-ink-3">回忆</div>
        {mem.error ? (
          <div className="text-xs text-err">{mem.error}</div>
        ) : (
          <div className="flex flex-col gap-1">
            {mem.chosen.map((c, i) => {
              const zh = c.card_id ? look?.zh.items[c.card_id] : undefined;
              const tip = zh?.text && (
                <>
                  <div className="text-[13px] font-semibold text-ink">{zh.name}</div>
                  {c.name && <div className="jp text-[11.5px] text-ink-3">{c.name}</div>}
                  <TipSection title="技能卡效果">
                    <span className="whitespace-pre-line">{zh.text}</span>
                  </TipSection>
                </>
              );
              return (
                <RowTip key={i} content={tip || null}>
                  <div className="flex items-center gap-2 rounded-lg border border-line bg-white px-2 py-1">
                    {c.card_id && <Thumb asset={zh?.asset} className="size-7" />}
                    <span className="rounded bg-chip px-1.5 text-[11px] text-ink-2">{TIMING_LABEL[String(c.timing)] ?? "?"}</span>
                    <span className="min-w-0 flex-1 truncate">
                      <span className={cx(!zh?.name && "jp")}>
                        {zh?.name ?? c.name ?? "?"}
                        {"+".repeat(c.upgrade || 0)}
                      </span>
                      {zh?.name && c.name && <span className="jp ml-1.5 text-[11px] text-ink-3">{c.name}</span>}
                    </span>
                    {!compact && c.role && <span className="max-w-[45%] truncate text-[11px] text-ink-3">{c.role}</span>}
                  </div>
                </RowTip>
              );
            })}
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
