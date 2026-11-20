// 对话里的卡片（设计稿 §5）：编成、对局、确认、信息、出错。
import { useState } from "react";
import type { Card } from "../lib/chat";
import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { useHiro, type Envelope } from "../lib/hiro";
import { ATTR_STYLE, KIND_LABEL, TIMING_LABEL, actionLabel, pageLabel, sourceLabel } from "../lib/labels";
import { useJob } from "../lib/useJob";
import { JobFrame, ScoreStrip, StatePill, lastReport } from "./Job";
import { PlanView } from "./PlanView";
import { Button, Panel, SourceTag, cx } from "./ui";

type Of<T extends Card["type"]> = Extract<Card, { type: T }>;

export function ConfirmCard({ card, onResolved }: { card: Of<"confirm">; onResolved: (r: Envelope, action: "confirm" | "cancel") => void }) {
  const call = useHiro();
  const [busy, setBusy] = useState(false);
  const act = async (action: "confirm" | "cancel") => {
    setBusy(true);
    try {
      onResolved(await call([action, card.pending.id]), action);
    } finally {
      setBusy(false);
    }
  };
  const done = card.status !== "waiting";
  if (done) {
    const [Icon, text, tone] =
      card.status === "confirmed" ? [CheckCircle2, "已开始", "text-good"] : card.status === "cancelled" ? [XCircle, "已取消", "text-ink-3"] : [AlertTriangle, "没能开始", "text-critical"];
    return (
      <div className="flex items-center gap-2 text-[12.5px] text-ink-2">
        <Icon className={cx("size-4 flex-none", tone)} />
        <span className="min-w-0 truncate">{card.pending.summary}</span>
        <span className={cx("flex-none font-medium", tone)}>{text}</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-4 rounded-xl border border-brand bg-brand-tint px-4 py-3 shadow-[0_0_0_3px_var(--color-brand-soft)]">
      <div className="min-w-0 flex-1">
        <b className="block text-[13.5px]">{card.pending.summary}</b>
        <span className="text-[12px] text-ink-2">这会动游戏，你点了才开始。</span>
      </div>
      <Button primary disabled={busy} onClick={() => act("confirm")}>
        开始
      </Button>
      <Button disabled={busy} onClick={() => act("cancel")}>
        先不
      </Button>
    </div>
  );
}

export function ErrorCard({ card, onRetry, onTakeover }: { card: Of<"error">; onRetry?: () => void; onTakeover?: () => void }) {
  return (
    <div role="alert" className="flex gap-3 rounded-xl border border-critical/30 bg-err-t px-4 py-3">
      <AlertTriangle className="mt-0.5 size-4 flex-none text-critical" />
      <div className="min-w-0 flex-1">
        <b className="block text-[13px] text-critical">{card.error.message}</b>
        {card.error.hint && <div className="mt-0.5 text-[12.5px] text-ink-2">{card.error.hint}</div>}
        {(onRetry || onTakeover) && (
          <div className="mt-2 flex gap-2">
            {onRetry && <Button onClick={onRetry}>重试</Button>}
            {onTakeover && <Button onClick={onTakeover}>我来接手</Button>}
          </div>
        )}
      </div>
    </div>
  );
}

export function RunCard({ card, onOpen }: { card: Of<"run">; onOpen: (jobId: string) => void }) {
  const { job, state, steps, lastFrame, events } = useJob(card.jobId);
  const recent = steps.slice(-3).reverse();
  const end = [...events].reverse().find((e) => ["done", "failed", "stopped"].includes(e.type));
  const imported = [...events].reverse().find((e) => e.type === "imported" || e.type === "import_failed");
  const logs = events.filter((e) => e.type === "log").slice(-2);
  const exams = lastReport(events);
  const live = state === "running" || state === "paused" || state === "takeover";
  return (
    <div className="flex gap-4 rounded-xl border border-line bg-surface p-3.5">
      <JobFrame frame={lastFrame} live={live} className="w-[86px]" />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <b className="min-w-0 truncate text-[13.5px]">{job?.summary ?? KIND_LABEL[job?.kind ?? ""] ?? "对局"}</b>
          <StatePill state={state} />
        </div>
        {steps.length > 0 && (
          <span className="text-[12px] text-ink-3">
            {pageLabel(steps[steps.length - 1]!.page)} · 第 {steps.length} 步
          </span>
        )}
        {exams && <ScoreStrip exams={exams} />}
        {!exams &&
          recent.map((s) => (
            <span key={s.seq} className="flex items-center gap-1.5 text-[12px] text-ink-2">
              <SourceTag label={sourceLabel(s.source)} />
              <span className="min-w-0 truncate">{s.text || actionLabel(s.action)}</span>
            </span>
          ))}
        {!exams &&
          recent.length === 0 &&
          logs.map((l) => (
            <span key={l.seq} className="truncate text-[12px] text-ink-2">
              {l.text}
            </span>
          ))}
        {imported && <span className={cx("text-[12px]", imported.type === "import_failed" ? "text-critical" : "text-good")}>{imported.text}</span>}
        {end && <span className={cx("text-[12px]", end.type === "failed" ? "text-critical" : "text-good")}>{end.text}</span>}
        <div className="mt-auto pt-1">
          <Button onClick={() => onOpen(card.jobId)}>打开这局的驾驶舱</Button>
        </div>
      </div>
    </div>
  );
}

function SupportTile({ it }: { it: Record<string, unknown> }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-line bg-surface px-2.5 py-1.5">
      <span className={cx("rounded px-1.5 text-[11px] font-bold", ATTR_STYLE[String(it.attr)] ?? "bg-chip text-ink-2")}>{String(it.attr ?? "?")}</span>
      <span className="jp min-w-0 flex-1 truncate">{String(it.name ?? "（没认出）")}</span>
      <span className="text-[11px] text-ink-3">
        {String(it.rarity ?? "")} Lv{String(it.level ?? "?")} · 突破 {String(it.breaks ?? "?")}
      </span>
    </div>
  );
}

function MemoryTile({ it }: { it: Record<string, unknown> }) {
  const st = (it.stats ?? {}) as Record<string, number | null>;
  return (
    <div className="rounded-lg border border-line bg-surface px-2.5 py-1.5">
      <div className="flex items-center gap-2">
        <span className="rounded bg-surface-3 px-1.5 text-[11px] text-ink-2">{TIMING_LABEL[String(it.timing)] ?? "?"}</span>
        <span className="jp min-w-0 flex-1 truncate">{String(it.card ?? "（没认出）")}</span>
      </div>
      <div className="mt-0.5 text-[11px] text-ink-3">
        Vo {st.Vo ?? "?"} · Da {st.Da ?? "?"} · Vi {st.Vi ?? "?"}
      </div>
    </div>
  );
}

function SettingTile({ it }: { it: Record<string, unknown> }) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-line bg-surface px-2.5 py-1.5">
      <span>{String(it.item)}</span>
      <b className={cx("text-xs", it.state === "ON" ? "text-good" : it.state === "OFF" ? "text-critical" : "text-ink-3")}>{it.state == null ? "读不到" : String(it.state)}</b>
    </div>
  );
}

export function InfoCard({ card, onOpen }: { card: Of<"info">; onOpen?: (kind: Of<"info">["kind"]) => void }) {
  const shown = card.items.slice(0, 6);
  const Tile = card.kind === "memories" ? MemoryTile : card.kind === "game_settings" ? SettingTile : SupportTile;
  return (
    <Panel>
      <div className="mb-2 flex items-baseline gap-2">
        <b>{card.title}</b>
        <span className="text-xs text-ink-3">共 {card.items.length} 条{card.scannedAt ? ` · 上次扫描 ${card.scannedAt.slice(5, 16)}` : ""}</span>
      </div>
      {card.items.length === 0 ? (
        <div className="text-xs text-ink-2">{card.hint ?? "没有符合的"}</div>
      ) : (
        <div className="grid grid-cols-2 gap-1.5">
          {shown.map((it, i) => (
            <Tile key={i} it={it} />
          ))}
        </div>
      )}
      {onOpen && card.items.length > shown.length && (
        <button type="button" className="mt-2 text-[12px] font-semibold text-brand-deep hover:underline" onClick={() => onOpen(card.kind)}>
          看全部 {card.items.length} 条 ›
        </button>
      )}
    </Panel>
  );
}

export function FormationCard({ card, onOpen }: { card: Of<"formation">; onOpen?: () => void }) {
  return (
    <Panel>
      <b>开局编成方案</b>
      <div className="mt-2">
        <PlanView plan={card.plan} compact />
      </div>
      {onOpen && (
        <button type="button" className="mt-2 text-[12px] font-semibold text-brand-deep hover:underline" onClick={onOpen}>
          打开编成页（看理由、写进游戏）›
        </button>
      )}
    </Panel>
  );
}
