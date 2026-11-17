// 快捷指令：没配模型时只能点这些（设计稿 §8），配了模型时也在输入框下面当建议问题。
import type { Card } from "./chat";
import type { Caller, Envelope } from "./hiro";

export interface Quick {
  label: string;
  args: string[];
  info?: { title: string; kind: "supports" | "memories" | "idols" | "summary" };
}

export const QUICK: Quick[] = [
  { label: "帮我领日常奖励", args: ["run", "start", "daily"] },
  { label: "接着打进行中的培育", args: ["run", "start", "hif", "--command", "resume"] },
  { label: "开一局 H.I.F 选拔", args: ["run", "start", "hif", "--command", "new", "--mode", "qualify"] },
  { label: "我的 SSR 支援卡", args: ["info", "supports", "--rarity", "SSR"], info: { title: "SSR 支援卡", kind: "supports" } },
  { label: "读首页数值", args: ["run", "start", "scan-account"] },
  { label: "检查游戏设置", args: ["run", "start", "check-settings"] },
  { label: "扫描支援卡", args: ["run", "start", "scan-supports"] },
  { label: "扫描回忆", args: ["run", "start", "scan-memories"] },
];

/** 一条命令的结果 → 卡片：待确认 → 确认卡；查询 → 信息卡；失败 → 出错卡；开了任务 → 对局卡。 */
export function cardsFor(r: Envelope, info?: Quick["info"]): Card[] {
  if (!r.ok) return [{ type: "error", error: r.error ?? { code: "error", message: "出错了" } }];
  if (r.pending) return [{ type: "confirm", pending: r.pending, status: "waiting" }];
  const d = (r.data ?? {}) as Record<string, unknown>;
  if (d.job && typeof d.job === "object") return [{ type: "run", jobId: (d.job as { id: string }).id }];
  if (info && Array.isArray(d.items)) {
    return [{ type: "info", title: info.title, kind: info.kind, items: d.items as Record<string, unknown>[], scannedAt: d.scanned_at as string | null, hint: d.hint as string | undefined }];
  }
  return [];
}

export async function runQuick(call: Caller, q: Quick): Promise<Card[]> {
  return cardsFor(await call(q.args), q.info);
}
