// 信息页（设计稿 §5，画板 q2-b 的样子）：卡片网格 + 筛选，顶部写上次扫描时间。
import { useEffect, useMemo, useState } from "react";
import { Chip, Empty, cx } from "../components/ui";
import { HiroError, data, spriteUrl, useHiro, type Listing } from "../lib/hiro";
import { ATTR_STYLE, TIMING_LABEL } from "../lib/labels";

type Kind = "supports" | "memories" | "idols" | "affection" | "catalog";
type Row = Record<string, unknown>;

const TITLE: Record<Kind, string> = { supports: "支援卡", memories: "回忆", idols: "偶像卡", affection: "好感度", catalog: "资料库" };

interface Filter {
  label: string;
  test: (r: Row) => boolean;
}

const FILTERS: Partial<Record<Kind, Filter[]>> = {
  supports: [
    ...["SSR", "SR", "R"].map((x) => ({ label: x, test: (r: Row) => r.rarity === x })),
    ...["Vo", "Da", "Vi"].map((x) => ({ label: x, test: (r: Row) => r.attr === x })),
  ],
  memories: ["start", "mid", "final"].map((x) => ({ label: TIMING_LABEL[x]!, test: (r: Row) => r.timing === x })),
  idols: ["SSR", "SR", "R"].map((x) => ({ label: x, test: (r: Row) => r.rarity === x })),
};

function SupportCell({ r }: { r: Row }) {
  return (
    <div className="flex gap-2.5 rounded-xl border border-line bg-panel p-2.5">
      <div className={cx("grid size-[52px] flex-none place-items-center rounded-[10px] text-sm font-bold", ATTR_STYLE[String(r.attr)] ?? "bg-chip text-ink-2")}>{String(r.attr ?? "?")}</div>
      <div className="min-w-0">
        <b className="jp block truncate text-[12.5px]">{String(r.name ?? "（没认出）")}</b>
        <div className="mt-1 flex flex-wrap gap-1 text-[10.5px]">
          <span className="rounded bg-[#fff1c9] px-1.5 text-[#8a6200]">{String(r.rarity ?? "?")}</span>
          <span className="rounded bg-chip px-1.5 text-ink-2">Lv {String(r.level ?? "?")}</span>
          <span className="rounded bg-chip px-1.5 text-ink-2">突破 {String(r.breaks ?? "?")}</span>
        </div>
      </div>
    </div>
  );
}

function MemoryCell({ r }: { r: Row }) {
  const st = (r.stats ?? {}) as Record<string, number | null>;
  const ab = (r.abilities ?? []) as string[];
  return (
    <div className="rounded-xl border border-line bg-panel p-2.5">
      <div className="flex items-center gap-2">
        <span className="rounded bg-chip px-1.5 text-[11px] text-ink-2">{TIMING_LABEL[String(r.timing)] ?? "?"}</span>
        <b className="jp min-w-0 flex-1 truncate text-[12.5px]">{String(r.card ?? "（没认出）")}</b>
      </div>
      <div className="mt-1 flex gap-1 text-[11px]">
        <span className="rounded bg-vo-t px-1.5 text-vo">Vo {st.Vo ?? "?"}</span>
        <span className="rounded bg-da-t px-1.5 text-da">Da {st.Da ?? "?"}</span>
        <span className="rounded bg-vi-t px-1.5 text-vi">Vi {st.Vi ?? "?"}</span>
      </div>
      {ab.length > 0 && <div className="jp mt-1 line-clamp-2 text-[11px] text-ink-2">{ab.join(" / ")}</div>}
    </div>
  );
}

function GenericCell({ r }: { r: Row }) {
  return (
    <div className="flex gap-2.5 rounded-xl border border-line bg-panel p-2.5">
      {typeof r.asset === "string" && <img src={spriteUrl(r.asset)} alt="" className="size-[52px] flex-none rounded-[10px]" />}
      <div className="min-w-0">
        <b className="jp block truncate text-[12.5px]">{String(r.name ?? r.idol ?? r.id ?? "?")}</b>
        <div className="text-[11.5px] text-ink-2">{[r.rarity, r.plan, r.level != null && `Lv ${r.level}`].filter(Boolean).join(" · ")}</div>
        {typeof r.text === "string" && <div className="jp mt-0.5 line-clamp-2 text-[11px] text-ink-3">{r.text}</div>}
      </div>
    </div>
  );
}

const CELL: Partial<Record<Kind, (p: { r: Row }) => React.ReactElement>> = { supports: SupportCell, memories: MemoryCell };

export function InfoPage({ kind, onAsk }: { kind: Kind; onAsk: (text: string) => void }) {
  const call = useHiro();
  const [listing, setListing] = useState<Listing | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [on, setOn] = useState<string | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    setListing(null);
    setErr(null);
    setOn(null);
    if (kind === "catalog") return;
    data<Listing>(call, ["info", kind])
      .then(setListing)
      .catch((e) => setErr(e instanceof HiroError ? `${e.error.message}。${e.error.hint ?? ""}` : String(e)));
  }, [kind, call]);

  useEffect(() => {
    if (kind !== "catalog") return;
    if (!q.trim()) {
      setListing({ kind, scanned_at: null, items: [] });
      return;
    }
    const t = setTimeout(() => {
      data<Listing>(call, ["catalog", "search", q.trim()])
        .then((l) => setListing({ ...l, scanned_at: null }))
        .catch((e) => setErr(e instanceof HiroError ? `${e.error.message}。${e.error.hint ?? ""}` : String(e)));
    }, 250);
    return () => clearTimeout(t);
  }, [kind, q, call]);

  const filters = FILTERS[kind] ?? [];
  const items = useMemo(() => {
    const f = filters.find((x) => x.label === on);
    return (listing?.items ?? []).filter((r) => !f || f.test(r));
  }, [listing, on, filters]);
  const Cell = CELL[kind] ?? GenericCell;

  return (
    <div className="flex h-full flex-col overflow-hidden px-6 py-5">
      <div className="mb-3 flex items-center gap-2.5">
        <h1 className="m-0 text-xl font-bold">{TITLE[kind]}</h1>
        {listing && kind !== "catalog" && (
          <span className="ml-auto text-xs text-ink-3">
            {listing.scanned_at ? `上次扫描：${listing.scanned_at.slice(0, 16)}${listing.complete === false ? " · 没扫全" : ""}` : "还没扫描"}
          </span>
        )}
      </div>
      {kind === "catalog" && (
        <input
          className="mb-3 h-9 rounded-lg border border-line bg-white px-3 outline-none focus:border-acc"
          placeholder="搜卡名或效果，例如 エウレカ、好調"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          autoFocus
        />
      )}
      {filters.length > 0 && (
        <div className="mb-3.5 flex flex-wrap gap-1.5">
          <Chip on={on === null} onClick={() => setOn(null)}>
            全部
          </Chip>
          {filters.map((f) => (
            <Chip key={f.label} on={on === f.label} onClick={() => setOn(on === f.label ? null : f.label)}>
              {f.label}
            </Chip>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto pb-6">
        {err && <Empty>{err}</Empty>}
        {!err && listing && listing.items.length === 0 && kind !== "catalog" && (
          <Empty>
            <div>{listing.hint ?? "没有数据"}</div>
            <button type="button" className="mt-2 text-xs font-bold text-acc" onClick={() => onAsk(`扫描${TITLE[kind]}`)}>
              让小广去扫描 ›
            </button>
          </Empty>
        )}
        {!err && listing && listing.items.length === 0 && kind === "catalog" && q.trim() && <Empty>没搜到</Empty>}
        <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
          {items.map((r, i) => (
            <Cell key={String(r.id ?? i)} r={r} />
          ))}
        </div>
      </div>
    </div>
  );
}
