// 驾驶舱（设计简报 §5）：实时画面 + 步骤时间线（理由 + 谁定的）；右边状态和控制；结束后成绩条和复盘。
import { ArrowLeft } from "lucide-react";
import { JobControls, JobFrame, ReviewButton, ScoreStrip, StatePill, StepRow, lastReport } from "../components/Job";
import { KIND_LABEL, pageLabel } from "../lib/labels";
import { useJob } from "../lib/useJob";

export function Cockpit({ jobId, onBack }: { jobId: string; onBack: () => void }) {
  const { job, state, steps, events, lastFrame, refresh } = useJob(jobId);
  const live = state === "running" || state === "paused" || state === "takeover" || state === "stopping";
  const feed = [...steps].reverse().slice(0, 60);
  const tail = [...events].reverse().find((e) => e.type !== "step" && e.type !== "report");
  const exams = lastReport(events);
  const pausable = job?.pausable !== false;
  return (
    <div className="flex min-h-0 min-w-0 flex-1 bg-surface">
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 flex-none items-center gap-3 border-b border-line px-6">
          <button type="button" className="inline-flex items-center gap-1 text-[12.5px] text-ink-3 hover:text-ink" onClick={onBack} aria-label="返回对话">
            <ArrowLeft className="size-4" />
            返回
          </button>
          <b className="min-w-0 truncate text-[14px]">{job?.summary ?? KIND_LABEL[job?.kind ?? ""] ?? "对局"}</b>
          <StatePill state={state} />
        </header>
        <div className="flex min-h-0 flex-1 gap-6 px-6 py-5">
          <JobFrame frame={lastFrame} live={live} className="w-[300px] self-start" />
          <div className="flex min-w-0 flex-1 flex-col">
            {exams && (
              <section className="mb-4 rounded-xl border border-line bg-surface-2 px-4 py-3.5">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[12px] font-medium text-ink-2">成绩</span>
                  <ReviewButton jobId={jobId} />
                </div>
                <ScoreStrip exams={exams} />
              </section>
            )}
            {!exams && !live && state && (
              <div className="mb-4 flex items-center justify-between rounded-xl border border-line bg-surface-2 px-4 py-3">
                <span className="text-ink-2">{tail?.text ?? "这局已经结束"}</span>
                {steps.length > 0 && <ReviewButton jobId={jobId} />}
              </div>
            )}
            <h2 className="m-0 mb-1 text-[12px] font-medium text-ink-3">初星助手每一步做了什么</h2>
            <ol className="m-0 min-h-0 flex-1 list-none divide-y divide-line overflow-auto p-0 pr-1">
              {feed.length === 0 && <li className="py-6 text-ink-3">{tail?.text ?? "还没有步骤"}</li>}
              {feed.map((e, i) => (
                <StepRow key={e.seq} e={e} now={i === 0 && live} />
              ))}
            </ol>
          </div>
        </div>
      </div>
      <aside className="flex w-[260px] flex-none flex-col gap-5 border-l border-line bg-surface-2 px-5 py-5">
        <section>
          <h3 className="m-0 mb-2 text-[12px] font-medium text-ink-3">现在</h3>
          <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-[12.5px]">
            <dt className="text-ink-3">页面</dt>
            <dd className="m-0 text-right font-semibold">{pageLabel(steps[steps.length - 1]?.page)}</dd>
            <dt className="text-ink-3">步数</dt>
            <dd className="disp m-0 text-right text-[16px]">{steps.length}</dd>
          </dl>
          {tail && <p className="m-0 mt-2 line-clamp-3 text-[12px] text-ink-3">{tail.text}</p>}
        </section>
        <section>
          <h3 className="m-0 mb-2 text-[12px] font-medium text-ink-3">操作</h3>
          {live ? <JobControls jobId={jobId} state={state} pausable={pausable} onDone={refresh} /> : <p className="m-0 text-[12px] text-ink-3">这局已经结束。</p>}
        </section>
      </aside>
    </div>
  );
}
