// 驾驶舱（设计稿 §5，画板 q1-b）：实时画面、每一步做了什么和理由（谁定的）、状态、暂停 / 我来接手 / 继续 / 停止。
import { useState } from "react";
import { Button, Panel, SourceTag, cx } from "../components/ui";
import { frameUrl, useHiro, type JobEvent } from "../lib/hiro";
import { KIND_LABEL, STATE_LABEL, clock, pageLabel, sourceLabel } from "../lib/labels";
import { useJob } from "../lib/useJob";

function StepRow({ e, now }: { e: JobEvent; now?: boolean }) {
  return (
    <div className={cx("grid grid-cols-[48px_1fr] gap-x-2.5 gap-y-0.5 rounded-[10px] border bg-panel px-3 py-2.5", now ? "border-acc shadow-[0_0_0_3px_var(--color-acc-t)]" : "border-line")}>
      <span className="row-span-2 text-[11.5px] text-ink-3">{now ? "现在" : clock(e.t)}</span>
      <span className="font-medium">
        {pageLabel(e.page)}
        {e.action ? ` · ${e.action}` : ""}
        <SourceTag label={sourceLabel(e.source)} />
      </span>
      <span className="text-xs text-ink-2">
        <b className="text-acc">小广：</b>
        {e.text || "（没写理由）"}
      </span>
    </div>
  );
}

export function Cockpit({ jobId, onBack }: { jobId: string; onBack: () => void }) {
  const call = useHiro();
  const { job, state, steps, events, lastFrame, refresh } = useJob(jobId);
  const [busy, setBusy] = useState(false);
  const img = frameUrl(lastFrame);
  const live = state === "running" || state === "paused" || state === "takeover";
  const pausable = job?.pausable !== false;
  const control = async (action: string) => {
    setBusy(true);
    try {
      await call(["run", action, "--job", jobId]);
      refresh();
    } finally {
      setBusy(false);
    }
  };
  const feed = [...steps].reverse().slice(0, 40);
  const tail = events.filter((e) => e.type !== "step").slice(-1)[0];
  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2.5 px-5 pt-3.5 pb-2.5">
          <button type="button" className="text-ink-3 hover:text-ink" onClick={onBack} aria-label="返回对话">
            ‹ 返回
          </button>
          <h1 className="m-0 text-[17px] font-bold">{job?.summary ?? KIND_LABEL[job?.kind ?? ""] ?? "对局"}</h1>
          <span className="rounded-md bg-ink px-2.5 py-0.5 text-xs text-white">{STATE_LABEL[state ?? ""] ?? "连接中"}</span>
        </div>
        <div className="flex min-h-0 flex-1 gap-4 px-5 pb-5">
          <div className="relative h-[533px] w-[300px] flex-none overflow-hidden rounded-xl border border-line bg-[#1d1915]">
            {img ? <img src={img} alt="游戏画面" className="size-full object-cover" /> : <div className="grid size-full place-items-center text-ink-3">还没有画面</div>}
            <span className="absolute top-2 left-2 rounded bg-black/55 px-1.5 text-[11px] text-white">{live ? "实时画面" : "最后一帧"}</span>
          </div>
          <div className="flex min-w-0 flex-1 flex-col gap-2 overflow-auto">
            <h2 className="m-0 text-xs font-medium text-ink-3">小广每一步做了什么</h2>
            {feed.length === 0 && <div className="text-ink-3">{tail?.text ?? "还没有步骤"}</div>}
            {feed.map((e, i) => (
              <StepRow key={e.seq} e={e} now={i === 0 && live} />
            ))}
          </div>
        </div>
      </div>
      <div className="flex w-[292px] flex-col gap-3.5 border-l border-line bg-panel p-4">
        <Panel>
          <h3 className="m-0 mb-2 text-xs font-medium text-ink-3">当前状态</h3>
          <div className="flex justify-between text-ink-2">
            <span>页面</span>
            <b className="text-ink">{pageLabel(steps[steps.length - 1]?.page)}</b>
          </div>
          <div className="mt-1.5 flex justify-between text-ink-2">
            <span>步数</span>
            <b className="text-ink">{steps.length}</b>
          </div>
          {tail && (
            <div className="mt-2 truncate text-xs text-ink-3" title={tail.text}>
              {tail.text}
            </div>
          )}
        </Panel>
        <Panel>
          <h3 className="m-0 mb-2 text-xs font-medium text-ink-3">操作</h3>
          <div className="grid grid-cols-2 gap-2">
            {state === "running" && pausable && (
              <Button disabled={busy} onClick={() => control("pause")}>
                暂停
              </Button>
            )}
            {state === "running" && pausable && (
              <Button disabled={busy} onClick={() => control("takeover")}>
                我来接手
              </Button>
            )}
            {(state === "paused" || state === "takeover") && (
              <Button primary disabled={busy} onClick={() => control("resume")}>
                继续自动打
              </Button>
            )}
            {live && (
              <Button disabled={busy} onClick={() => control("stop")}>
                停止
              </Button>
            )}
          </div>
          {state === "takeover" && <div className="mt-2 text-xs text-ink-2">自动操作已停下，你在游戏里点完后按「继续自动打」，小广会从当前画面接着打。</div>}
          {!live && <div className="mt-1 text-xs text-ink-3">这局已经结束。</div>}
          {live && !pausable && <div className="mt-2 text-xs text-ink-3">这个任务不能中途暂停；要自己操作就先停止（不会放弃培育）。</div>}
        </Panel>
      </div>
    </div>
  );
}
