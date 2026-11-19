import { threadJob, type PageKind, type Thread } from "../lib/chat";
import type { Summary } from "../lib/hiro";
import { Dot, cx } from "./ui";

export type View = { name: "chat" } | { name: "cockpit"; jobId: string } | { name: "info"; kind: PageKind } | { name: "plan" } | { name: "settings" };

export function Sidebar(p: {
  threads: Thread[];
  active: string;
  liveJob: string | null;
  summary: Summary | null;
  model: string;
  device: string;
  onNew: () => void;
  onPick: (id: string) => void;
  onSettings: () => void;
}) {
  const acct = p.summary?.account;
  return (
    <aside className="flex w-[236px] flex-none flex-col border-r border-line bg-panel p-3">
      <div className="flex items-center gap-2 px-1.5 pt-1 pb-3 text-[15px] font-bold">
        <i className="inline-block size-[22px] rounded-md bg-acc" />
        初星助手
      </div>
      <button type="button" className="flex h-[34px] items-center gap-1.5 rounded-lg border border-line bg-white px-2.5 font-medium hover:bg-chip" onClick={p.onNew}>
        ＋ 新对话
      </button>
      <div className="mx-1.5 mt-3.5 mb-1 text-[11px] text-ink-3">对话和任务</div>
      <nav className="-mx-1 flex min-h-0 flex-1 flex-col gap-0.5 overflow-auto px-1">
        {p.threads.map((t) => {
          const live = !!p.liveJob && threadJob(t) === p.liveJob;
          return (
            <button
              type="button"
              key={t.id}
              onClick={() => p.onPick(t.id)}
              className={cx("flex items-center gap-2 rounded-[7px] px-2 py-[7px] text-left", t.id === p.active ? "bg-acc-t" : "hover:bg-chip")}
            >
              <span className="min-w-0 flex-1 truncate">{t.title}</span>
              {live && <small className="text-[10.5px] font-bold text-acc">进行中</small>}
            </button>
          );
        })}
      </nav>
      <div className="mt-2 flex flex-col gap-1 border-t border-line pt-2.5 text-xs text-ink-2">
        {acct ? (
          <>
            <span>
              PLv <b className="text-ink">{acct.plv ?? "?"}</b> · AP <b className="text-ink">{acct.ap ?? "?"}{acct.ap_max ? `/${acct.ap_max}` : ""}</b>
            </span>
            <span>
              ジュエル <b className="text-ink">{acct.jewel?.toLocaleString() ?? "?"}</b>
            </span>
          </>
        ) : (
          <span className="text-ink-3">还没读首页数值</span>
        )}
        <span className="flex items-center gap-1.5">
          <Dot tone={p.device ? "ok" : "off"} /> {p.device || "游戏未连接"}
        </span>
        <button type="button" className="text-left hover:text-ink" onClick={p.onSettings}>
          模型：{p.model} ›
        </button>
      </div>
    </aside>
  );
}
