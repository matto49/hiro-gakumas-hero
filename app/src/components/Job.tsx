// 任务显示的零件：状态胶囊、游戏画面框、步骤行、成绩条、复盘按钮、控制按钮。驾驶舱、右栏、对局卡共用。
import { FileSearch, ImageOff, Pause, Play, Square, Hand } from "lucide-react";
import { useState } from "react";
import { frameUrl, homeUrl, useHiro, type JobEvent } from "../lib/hiro";
import { STATE_LABEL, actionLabel, clock, pageLabel, sourceLabel } from "../lib/labels";
import { Button, Dot, SourceTag, cx } from "./ui";

const LIVE = new Set(["running", "paused", "takeover", "stopping"]);

export function StatePill({ state }: { state: string | null }) {
  const s = state ?? "";
  const tone = s === "running" ? "acc" : s === "done" ? "ok" : s === "failed" || s === "lost" ? "err" : s === "paused" || s === "takeover" ? "warn" : "off";
  const cls =
    s === "running"
      ? "border-brand/40 bg-brand-tint text-brand-ink"
      : s === "done"
        ? "border-good/30 bg-ok-t text-good"
        : s === "failed" || s === "lost"
          ? "border-critical/30 bg-err-t text-critical"
          : s === "paused" || s === "takeover"
            ? "border-warning/50 bg-warn-t text-warn"
            : "border-line-strong bg-surface-2 text-ink-2";
  return (
    <span className={cx("inline-flex h-6 flex-none items-center gap-1.5 rounded-full border px-2.5 text-[11.5px] font-medium", cls)}>
      <Dot tone={tone as "acc"} pulse={s === "running"} />
      {STATE_LABEL[s] ?? "连接中"}
    </span>
  );
}

export function JobFrame({ frame, live, className }: { frame: string | null; live?: boolean; className?: string }) {
  const img = frameUrl(frame);
  return (
    <div className={cx("relative aspect-[9/16] flex-none overflow-hidden rounded-lg border border-line bg-surface-3", className)}>
      {img ? (
        <img src={img} alt="游戏画面" className="size-full object-cover" />
      ) : (
        <div className="grid size-full place-items-center px-3 text-center text-[11.5px] text-ink-3">
          <span className="flex flex-col items-center gap-1.5">
            <ImageOff className="size-5 text-ink-3/70" />
            {live ? "等第一张画面" : "没有画面"}
          </span>
        </div>
      )}
      {img && <span className="absolute top-1.5 left-1.5 rounded bg-ink/60 px-1.5 text-[10.5px] text-surface">{live ? "实时" : "最后一帧"}</span>}
    </div>
  );
}

export function StepRow({ e, now }: { e: JobEvent; now?: boolean }) {
  const page = pageLabel(e.page);
  const action = actionLabel(e.action);
  return (
    <li className={cx("relative grid grid-cols-[44px_1fr] gap-x-3 py-2.5", now && "rounded-lg bg-brand-tint px-2 -mx-2")}>
      <span className="pt-px text-right text-[11.5px] text-ink-3 tabular-nums">{now ? "现在" : clock(e.t)}</span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center text-[13px] font-semibold text-ink">
          {page}
          {action && action !== page ? <span className="ml-1.5 font-normal text-ink-3">· {action}</span> : null}
          <SourceTag label={sourceLabel(e.source)} className="ml-1.5" />
        </div>
        <div className="mt-0.5 text-[12.5px] leading-relaxed text-ink-2">{e.text || "（没写理由）"}</div>
      </div>
    </li>
  );
}

export interface ExamScore {
  label?: string | null;
  score?: number | null;
}

export function ScoreStrip({ exams }: { exams: ExamScore[] }) {
  const shown = exams.filter((e) => e.score != null);
  if (!shown.length) return null;
  return (
    <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
      {shown.map((e, i) => (
        <div key={i} className="flex flex-col">
          <span className="text-[11px] text-ink-3">{(e.label ?? `第 ${i + 1} 场`).replace(/#.*$/, "")}</span>
          <span className={cx("disp leading-none", i === shown.length - 1 ? "text-[30px] text-brand-ink" : "text-[22px] text-ink")}>{e.score!.toLocaleString("en-US")}</span>
        </div>
      ))}
    </div>
  );
}

export function ReviewButton({ jobId }: { jobId: string }) {
  const call = useHiro();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const open = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await call(["run", "review", "--job", jobId]);
      if (!r.ok) setErr(r.error?.message ?? "复盘页没生成出来");
      else window.open(homeUrl((r.data as { url: string }).url), "_blank");
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="inline-flex items-center gap-2">
      <Button disabled={busy} onClick={open} aria-label="看复盘">
        <FileSearch className="size-3.5" />
        {busy ? "生成中…" : "看复盘"}
      </Button>
      {err && <span className="text-[12px] text-critical">{err}</span>}
    </span>
  );
}

export function JobControls({ jobId, state, pausable, onDone }: { jobId: string; state: string | null; pausable: boolean; onDone?: () => void }) {
  const call = useHiro();
  const [busy, setBusy] = useState(false);
  const live = LIVE.has(state ?? "");
  const control = async (action: string) => {
    setBusy(true);
    try {
      await call(["run", action, "--job", jobId]);
      onDone?.();
    } finally {
      setBusy(false);
    }
  };
  if (!live) return null;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {state === "running" && pausable && (
          <Button disabled={busy} onClick={() => control("pause")} aria-label="暂停">
            <Pause className="size-3.5" />
            暂停
          </Button>
        )}
        {state === "running" && pausable && (
          <Button disabled={busy} onClick={() => control("takeover")} aria-label="我来接手">
            <Hand className="size-3.5" />
            我来接手
          </Button>
        )}
        {(state === "paused" || state === "takeover") && (
          <Button primary disabled={busy} onClick={() => control("resume")} aria-label="继续自动打">
            <Play className="size-3.5" />
            继续自动打
          </Button>
        )}
        <Button disabled={busy} onClick={() => control("stop")} aria-label="停止">
          <Square className="size-3.5" />
          停止
        </Button>
      </div>
      {state === "takeover" && <p className="m-0 text-[12px] text-ink-2">自动操作已停下。你在游戏里点完，按「继续自动打」，初星助手会从当前画面接着打。</p>}
      {!pausable && <p className="m-0 text-[12px] text-ink-3">这个任务不能中途暂停；要自己操作就先停止（不会放弃培育）。</p>}
    </div>
  );
}

export function lastReport(events: JobEvent[]): ExamScore[] | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i] as JobEvent & { exams?: ExamScore[] };
    if (e.type === "report" && e.exams) return e.exams;
  }
  return null;
}
