// 右栏（设计简报 §5）：有任务时是当前任务（实时画面、阶段、最近几步、暂停 / 接手 / 停止）；没任务时是账号概况。
import { ArrowUpRight } from "lucide-react";
import type { PageKind } from "../lib/chat";
import type { Summary } from "../lib/hiro";
import { actionLabel, pageLabel, sourceLabel } from "../lib/labels";
import { useJob } from "../lib/useJob";
import { JobControls, JobFrame, StatePill } from "./Job";
import { StartTask } from "./StartTask";
import { SourceTag, cx } from "./ui";

function CurrentJob({ jobId, onOpenJob }: { jobId: string; onOpenJob: (id: string) => void }) {
  const { job, state, steps, lastFrame, refresh } = useJob(jobId);
  const recent = [...steps].reverse().slice(0, 3);
  return (
    <div className="flex flex-col gap-3.5 p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-medium text-ink-3">当前任务</span>
        <StatePill state={state} />
      </div>
      <b className="text-[13.5px] leading-snug">{job?.summary ?? "…"}</b>
      <JobFrame frame={lastFrame} live className="mx-auto w-[190px]" />
      <div className="flex items-baseline justify-between text-[12.5px]">
        <span className="text-ink-3">{pageLabel(steps[steps.length - 1]?.page)}</span>
        <span className="text-ink-3">
          第 <b className="disp text-[15px] text-ink">{steps.length}</b> 步
        </span>
      </div>
      {recent.length > 0 && (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
          {recent.map((s) => (
            <li key={s.seq} className="flex items-start gap-1.5 text-[12px] leading-snug text-ink-2">
              <SourceTag label={sourceLabel(s.source)} className="mt-px" />
              <span className="line-clamp-2 min-w-0">{s.text || actionLabel(s.action)}</span>
            </li>
          ))}
        </ul>
      )}
      <JobControls jobId={jobId} state={state} pausable={job?.pausable !== false} onDone={refresh} />
      <button type="button" onClick={() => onOpenJob(jobId)} className="inline-flex items-center gap-1 self-start text-[12.5px] font-semibold text-brand-ink hover:underline">
        打开驾驶舱
        <ArrowUpRight className="size-3.5" />
      </button>
    </div>
  );
}

const ROWS: [PageKind, string][] = [
  ["supports", "支援卡"],
  ["memories", "回忆"],
  ["idols", "偶像卡"],
  ["affection", "好感度"],
  ["game_settings", "游戏设置"],
];

function Account({ summary, onOpen, onOpenPlan, onOpenJob }: { summary: Summary | null; onOpen: (k: PageKind) => void; onOpenPlan: () => void; onOpenJob: (id: string) => void }) {
  const acct = summary?.account;
  const home = summary?.kinds.account;
  return (
    <div className="flex flex-col gap-4 p-4">
      <section>
        <div className="mb-2 flex items-baseline justify-between">
          <span className="text-[12px] font-medium text-ink-3">首页数值</span>
          <span className="text-[11px] text-ink-3">{home?.scanned_at ? home.scanned_at.slice(5, 16) : "没读过"}</span>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {(
            [
              ["PLv", acct?.plv, null],
              ["AP", acct?.ap, acct?.ap_max],
              ["ジュエル", acct?.jewel, null],
            ] as [string, number | undefined, number | undefined | null][]
          ).map(([k, v, max]) => (
            <div key={k} className="rounded-lg border border-line bg-surface px-2.5 py-2">
              <div className="jp text-[11px] text-ink-3">{k}</div>
              <div className="disp text-[19px] leading-tight">
                {v != null ? v.toLocaleString("en-US") : "—"}
                {max ? <span className="text-[13px] text-ink-3">/{max}</span> : null}
              </div>
            </div>
          ))}
        </div>
        <div className="mt-2.5">
          <StartTask label="读首页" args={["scan-account"]} onOpenJob={onOpenJob} />
        </div>
      </section>
      <section>
        <span className="text-[12px] font-medium text-ink-3">账号信息</span>
        <ul className="m-0 mt-1.5 flex list-none flex-col p-0">
          {ROWS.map(([k, label]) => {
            const s = summary?.kinds[k];
            const cant = k === "affection";
            return (
              <li key={k}>
                <button type="button" className="flex h-8 w-full items-center justify-between rounded-md px-2 text-left hover:bg-surface-3/60" onClick={() => onOpen(k)}>
                  <span>{label}</span>
                  <span className={cx("text-[11.5px]", s?.scanned_at ? "text-ink-2" : "text-ink-3")}>
                    {s?.scanned_at ? `${k === "game_settings" ? "" : `${s.count} · `}${s.scanned_at.slice(5, 16)}` : cant ? "还不能扫" : "没扫描"} ›
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>
      <button
        type="button"
        onClick={onOpenPlan}
        className="flex items-center justify-between rounded-lg border border-line bg-surface px-3 py-2.5 text-left transition-colors hover:border-brand"
      >
        <b className="text-[13px]">开局编成方案</b>
        <span className="text-[12px] text-brand-ink">出方案 / 写进游戏 ›</span>
      </button>
    </div>
  );
}

export function RightPanel({
  liveJob,
  summary,
  onOpenInfo,
  onOpenPlan,
  onOpenJob,
}: {
  liveJob: string | null;
  summary: Summary | null;
  onOpenInfo: (k: PageKind) => void;
  onOpenPlan: () => void;
  onOpenJob: (id: string) => void;
}) {
  return (
    <aside className="flex w-[288px] flex-none flex-col overflow-auto border-l border-line bg-surface-2">
      {liveJob ? <CurrentJob jobId={liveJob} onOpenJob={onOpenJob} /> : <Account summary={summary} onOpen={onOpenInfo} onOpenPlan={onOpenPlan} onOpenJob={onOpenJob} />}
    </aside>
  );
}
