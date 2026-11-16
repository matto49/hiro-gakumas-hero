// 右栏（设计稿 §5）：游戏画面 / 账号 / 资料库。
import { useEffect, useState } from "react";
import { data, frameUrl, spriteUrl, useHiro, type Summary } from "../lib/hiro";
import { STATE_LABEL, pageLabel } from "../lib/labels";
import { useJob } from "../lib/useJob";
import { cx } from "./ui";

type Tab = "screen" | "account" | "catalog";
type InfoKind = "supports" | "memories" | "idols" | "affection" | "catalog";

function Kv({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between px-4 py-[5px] text-ink-2">
      <span>{k}</span>
      <b className="text-ink">{v}</b>
    </div>
  );
}

function Screen({ jobId }: { jobId: string | null }) {
  const { job, state, steps, lastFrame } = useJob(jobId);
  const img = frameUrl(lastFrame);
  return (
    <>
      <div className="mx-auto mt-3 mb-2 h-[313px] w-[176px] overflow-hidden rounded-[10px] border border-line bg-[#1d1915]">
        {img ? <img src={img} alt="游戏画面" className="size-full object-cover" /> : <div className="grid size-full place-items-center px-4 text-center text-xs text-ink-3">{jobId ? "等画面" : "没有在跑的对局"}</div>}
      </div>
      {jobId && (
        <>
          <Kv k="对局" v={job?.summary ?? "…"} />
          <Kv k="状态" v={STATE_LABEL[state ?? ""] ?? "…"} />
          <Kv k="当前页面" v={pageLabel(steps[steps.length - 1]?.page)} />
        </>
      )}
    </>
  );
}

function Account({ summary, onOpen }: { summary: Summary | null; onOpen: (k: InfoKind) => void }) {
  const rows: [InfoKind, string][] = [
    ["idols", "偶像卡"],
    ["supports", "支援卡"],
    ["memories", "回忆"],
    ["affection", "好感度"],
  ];
  return (
    <div className="flex flex-col gap-1 p-3">
      {rows.map(([k, label]) => {
        const s = summary?.kinds[k];
        return (
          <button type="button" key={k} className="flex items-center justify-between rounded-lg px-2.5 py-2 text-left hover:bg-chip" onClick={() => onOpen(k)}>
            <span>{label}</span>
            <span className="text-xs text-ink-3">{s?.scanned_at ? `${s.count} · ${s.scanned_at.slice(5, 16)}` : "没扫描"} ›</span>
          </button>
        );
      })}
    </div>
  );
}

function CatalogMini({ onOpen }: { onOpen: () => void }) {
  const call = useHiro();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    if (!q.trim()) {
      setItems([]);
      return;
    }
    const t = setTimeout(() => {
      data<{ items: Record<string, unknown>[] }>(call, ["catalog", "search", q.trim()])
        .then((r) => {
          setItems(r.items.slice(0, 8));
          setMsg(null);
        })
        .catch((e) => setMsg(e.error?.hint ?? String(e)));
    }, 250);
    return () => clearTimeout(t);
  }, [q, call]);
  return (
    <div className="flex flex-col gap-2 p-3">
      <input className="h-8 rounded-lg border border-line bg-white px-2.5 outline-none focus:border-acc" placeholder="搜卡名或效果" value={q} onChange={(e) => setQ(e.target.value)} />
      {msg && <div className="text-xs text-ink-3">{msg}</div>}
      {items.map((it) => (
        <div key={String(it.id)} className="flex items-center gap-2">
          {typeof it.asset === "string" ? <img src={spriteUrl(it.asset)} alt="" className="size-8 rounded" /> : <span className="size-8 rounded bg-chip" />}
          <span className="jp min-w-0 flex-1 truncate">{String(it.name)}</span>
        </div>
      ))}
      <button type="button" className="text-left text-xs font-bold text-acc" onClick={onOpen}>
        打开资料库 ›
      </button>
    </div>
  );
}

export function RightPanel({ liveJob, summary, onOpenInfo }: { liveJob: string | null; summary: Summary | null; onOpenInfo: (k: InfoKind) => void }) {
  const [tab, setTab] = useState<Tab>("screen");
  const tabs: [Tab, string][] = [
    ["screen", "游戏画面"],
    ["account", "账号"],
    ["catalog", "资料库"],
  ];
  return (
    <aside className="flex w-[300px] flex-none flex-col border-l border-line bg-panel">
      <div className="flex gap-1 border-b border-line px-3 py-2.5">
        {tabs.map(([t, label]) => (
          <button type="button" key={t} className={cx("rounded-[7px] px-2.5 py-1", tab === t ? "bg-ink text-white" : "text-ink-2 hover:bg-chip")} onClick={() => setTab(t)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "screen" && <Screen jobId={liveJob} />}
      {tab === "account" && <Account summary={summary} onOpen={onOpenInfo} />}
      {tab === "catalog" && <CatalogMini onOpen={() => onOpenInfo("catalog")} />}
    </aside>
  );
}
