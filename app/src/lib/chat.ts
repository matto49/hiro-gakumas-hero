// 对话和卡片的数据模型（设计稿 §5）。
import type { CliError, Pending } from "./hiro";
import type { PlanSummary } from "./plan";

export type Card =
  | { type: "confirm"; pending: Pending; status: "waiting" | "confirmed" | "cancelled" | "failed" }
  | { type: "run"; jobId: string }
  | { type: "info"; title: string; kind: InfoKind; items: Record<string, unknown>[]; scannedAt?: string | null; hint?: string }
  | { type: "error"; error: CliError }
  | { type: "formation"; plan: PlanSummary };

export type InfoKind = "supports" | "memories" | "idols" | "affection" | "catalog" | "summary" | "game_settings";
/** 有自己一页的信息 */
export type PageKind = Exclude<InfoKind, "summary">;

export interface Message {
  id: string;
  role: "user" | "agent";
  text?: string;
  cards: Card[];
  at: number;
}

export interface Thread {
  id: string;
  title: string;
  messages: Message[];
  updatedAt: number;
}

let n = 0;
export function uid(prefix = "m"): string {
  n += 1;
  return `${prefix}-${Date.now().toString(36)}-${n}`;
}

export function newThread(title = "新对话"): Thread {
  return { id: uid("t"), title, messages: [], updatedAt: Date.now() };
}

/** 对话里最新的对局（有对局卡的话），侧栏和右栏用。 */
export function threadJob(t: Thread): string | null {
  for (let i = t.messages.length - 1; i >= 0; i--) {
    const cards = t.messages[i]!.cards;
    for (let j = cards.length - 1; j >= 0; j--) {
      const c = cards[j]!;
      if (c.type === "run") return c.jobId;
    }
  }
  return null;
}
