// 模型设置和对话后端（Go 侧 Assistant / Threads）。组件经 AssistantCtx 拿，测试里换成假的。
import { createContext, useContext } from "react";
import type { Card, Thread } from "./chat";
import type { Agent, Reply } from "./agent";

export interface Settings {
  /** deepseek 是缺省；none = 不用模型 */
  provider: "deepseek" | "codex" | "claude" | "none";
  model?: string;
  base_url?: string;
  has_key: boolean;
  wizard_done?: boolean;
}

export const DEFAULT_DEEPSEEK_MODEL = "deepseek-flash";

export interface Turn {
  role: "user" | "assistant";
  content: string;
}

export interface AssistantApi {
  settings: () => Promise<Settings>;
  save: (s: Settings, key: string) => Promise<void>;
  test: () => Promise<{ ok: boolean; message: string }>;
  /** 这个 Key 能用的 DeepSeek 模型（没 Key 或连不上时抛错） */
  models?: () => Promise<string[]>;
  ask: (history: Turn[], text: string) => Promise<Reply>;
  listThreads: () => Promise<Thread[]>;
  saveThread: (t: Thread) => Promise<void>;
  deleteThread: (id: string) => Promise<void>;
}

const mygo = () => import("../mygo");

export const realAssistant: AssistantApi = {
  settings: async () => (await mygo()).Assistant.settings() as Promise<Settings>,
  save: async (s, key) => (await mygo()).Assistant.save(s, key),
  test: async () => (await mygo()).Assistant.test(),
  models: async () => ((await (await mygo()).Assistant.models()) as string[]) ?? [],
  ask: async (h, t) => (await (await mygo()).Assistant.ask(h, t)) as unknown as Reply,
  listThreads: async () => ((await (await mygo()).Threads.list()) as unknown as Thread[]) ?? [],
  saveThread: async (t) => (await mygo()).Threads.save(t.id, t as never),
  deleteThread: async (id) => (await mygo()).Threads.delete(id),
};

export const AssistantCtx = createContext<AssistantApi>(realAssistant);

export function useAssistant(): AssistantApi {
  return useContext(AssistantCtx);
}

export const PROVIDER_LABEL: Record<Settings["provider"], string> = {
  deepseek: "DeepSeek",
  claude: "Claude",
  codex: "Codex",
  none: "不用模型",
};

/** 侧栏上的模型名：没法用的时候写清楚为什么只能点快捷指令。 */
export function modelLabel(s: Settings | null): string {
  if (!s) return "…";
  if (s.provider === "deepseek" && !s.has_key) return "DeepSeek（还没填 Key）";
  if (s.provider === "none") return "不用模型（快捷指令）";
  return PROVIDER_LABEL[s.provider] + (s.provider === "deepseek" && s.model ? ` · ${s.model}` : "");
}

/** 卡片写成一句话，放进交给模型的对话历史。 */
export function cardText(c: Card): string {
  switch (c.type) {
    case "confirm":
      return `[确认卡：${c.pending.summary}，${{ waiting: "等玩家确认", confirmed: "玩家已确认、已开始", cancelled: "玩家取消了", failed: "没能开始" }[c.status]}]`;
    case "run":
      return `[对局卡：任务 ${c.jobId}]`;
    case "info":
      return `[信息卡：${c.title}，${c.items.length} 条]`;
    case "error":
      return `[出错：${c.error.message}]`;
    case "formation":
      return `[编成卡：${c.plan.idol.name ?? "?"}，主属性 ${c.plan.main_attr ?? "?"}]`;
  }
}

export function historyOf(t: Thread, limit = 20): Turn[] {
  return t.messages.slice(-limit).map((m) => ({
    role: m.role === "user" ? "user" : "assistant",
    content: [m.text ?? "", ...m.cards.map(cardText)].filter(Boolean).join("\n"),
  }));
}

export function modelAgent(api: AssistantApi, s: Settings): Agent {
  return {
    quickOnly: false,
    label: modelLabel(s),
    ask: (text, thread) => api.ask(historyOf(thread), text),
  };
}
