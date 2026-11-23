// 左侧栏（设计简报 §5）：开局、编成方案、信息、设置；最近的对话；底部账号数值和模型。
import { BookOpen, ClipboardList, Cog, Heart, IdCard, Images, Layers, MessageSquarePlus, Play, Settings2, Sparkles } from "lucide-react";
import type { DeviceStatus } from "../lib/device";
import type { ComponentType } from "react";
import { threadJob, type PageKind, type Thread } from "../lib/chat";
import type { Summary } from "../lib/hiro";
import { Dot, Logo, cx } from "./ui";

export type View =
  | { name: "chat" }
  | { name: "start" }
  | { name: "cockpit"; jobId: string }
  | { name: "info"; kind: PageKind }
  | { name: "plan" }
  | { name: "settings" };

type Icon = ComponentType<{ className?: string }>;

const INFO: [PageKind, string, Icon][] = [
  ["supports", "支援卡", Layers],
  ["memories", "回忆", Images],
  ["idols", "偶像卡", IdCard],
  ["affection", "好感度", Heart],
  ["game_settings", "游戏设置", Cog],
  ["catalog", "资料库", BookOpen],
];

function NavItem({ icon: I, label, on, onClick, badge }: { icon: Icon; label: string; on: boolean; onClick: () => void; badge?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={on ? "page" : undefined}
      className={cx(
        "flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-[13px] transition-colors",
        on ? "bg-surface-3 font-semibold text-ink" : "text-ink-2 hover:bg-surface-3/60 hover:text-ink",
      )}
    >
      <I className={cx("size-4 flex-none", on ? "text-brand-ink" : "text-ink-3")} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {badge && <span className="text-[11px] text-ink-3">{badge}</span>}
    </button>
  );
}

function same(a: View, b: View): boolean {
  if (a.name !== b.name) return false;
  return a.name !== "info" || (b.name === "info" && a.kind === b.kind);
}

export function Sidebar(p: {
  view: View;
  threads: Thread[];
  active: string;
  liveJob: string | null;
  summary: Summary | null;
  model: string;
  device: DeviceStatus;
  onProbe: () => void;
  onNav: (v: View) => void;
  onNew: () => void;
  onPick: (id: string) => void;
}) {
  const acct = p.summary?.account;
  return (
    <aside className="flex w-[232px] flex-none flex-col border-r border-line bg-surface-2">
      <div className="flex items-center gap-2.5 px-4 pt-4 pb-3">
        <Logo />
        <span className="text-[15px] font-bold tracking-tight">初星助手</span>
      </div>
      <div className="flex flex-col gap-0.5 px-2.5">
        <button
          type="button"
          onClick={() => p.onNav({ name: "start" })}
          aria-current={p.view.name === "start" ? "page" : undefined}
          className={cx(
            "mb-1.5 flex h-9 items-center gap-2 rounded-full px-3.5 text-[13.5px] font-semibold transition-colors",
            p.view.name === "start"
              ? "bg-brand-deeper text-surface"
              : "bg-brand-deep text-surface shadow-[0_2px_0_var(--color-brand-deeper)] hover:bg-brand-deeper",
          )}
        >
          <Play className="size-4" />
          开局
        </button>
        <NavItem icon={ClipboardList} label="编成方案" on={p.view.name === "plan"} onClick={() => p.onNav({ name: "plan" })} />
        <div className="mx-2.5 mt-3 mb-1 text-[11px] font-medium text-ink-3">信息</div>
        {INFO.map(([k, label, I]) => (
          <NavItem
            key={k}
            icon={I}
            label={label}
            on={same(p.view, { name: "info", kind: k })}
            onClick={() => p.onNav({ name: "info", kind: k })}
            badge={k !== "catalog" && k !== "game_settings" && p.summary?.kinds[k]?.count ? String(p.summary.kinds[k]!.count) : undefined}
          />
        ))}
      </div>
      <div className="mx-5 mt-3 mb-1 flex items-center justify-between text-[11px] font-medium text-ink-3">
        对话
        <button type="button" onClick={p.onNew} className="inline-flex items-center gap-1 rounded px-1 text-ink-3 hover:text-brand-ink" aria-label="新对话">
          <MessageSquarePlus className="size-3.5" />
          新对话
        </button>
      </div>
      <nav className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-auto px-2.5 pb-2">
        {p.threads.map((t) => {
          const live = !!p.liveJob && threadJob(t) === p.liveJob;
          const on = p.view.name === "chat" && t.id === p.active;
          return (
            <button
              type="button"
              key={t.id}
              onClick={() => p.onPick(t.id)}
              className={cx(
                "flex h-8 items-center gap-2 rounded-md px-2.5 text-left text-[12.5px] transition-colors",
                on ? "bg-surface-3 font-medium text-ink" : "text-ink-2 hover:bg-surface-3/60 hover:text-ink",
              )}
            >
              {live ? <Dot tone="acc" pulse /> : <Sparkles className="size-3 flex-none text-ink-3/60" />}
              <span className="min-w-0 flex-1 truncate">{t.title}</span>
              {live && <small className="text-[10.5px] font-semibold text-brand-ink">进行中</small>}
            </button>
          );
        })}
      </nav>
      <div className="flex flex-col gap-1.5 border-t border-line px-4 py-3 text-[12px] text-ink-2">
        {acct ? (
          <div className="flex items-baseline gap-3">
            <span>
              PLv <b className="disp text-[15px] text-ink">{acct.plv ?? "?"}</b>
            </span>
            <span>
              AP <b className="disp text-[15px] text-ink">{acct.ap ?? "?"}</b>
              {acct.ap_max ? <span className="text-ink-3">/{acct.ap_max}</span> : null}
            </span>
            <span className="jp">
              ジュエル <b className="disp text-[15px] text-ink">{acct.jewel?.toLocaleString() ?? "?"}</b>
            </span>
          </div>
        ) : (
          <span className="text-ink-3">还没读首页数值</span>
        )}
        <button
          type="button"
          title={p.device.title || undefined}
          onClick={p.onProbe}
          className="flex items-center gap-1.5 text-left hover:text-ink"
        >
          <Dot tone={p.device.tone} /> <span className="min-w-0 truncate">{p.device.text}</span>
        </button>
        <button
          type="button"
          className={cx("flex items-center gap-1.5 text-left hover:text-ink", p.view.name === "settings" && "font-semibold text-ink")}
          onClick={() => p.onNav({ name: "settings" })}
        >
          <Settings2 className="size-3.5 flex-none text-ink-3" />
          <span className="min-w-0 truncate">模型：{p.model}</span>
          <span className="flex-none">›</span>
        </button>
      </div>
    </aside>
  );
}
