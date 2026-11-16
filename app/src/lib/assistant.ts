// 模型设置和对话后端（Go 侧 Assistant / Threads）。组件经 AssistantCtx 拿，测试里换成假的。
import { createContext, useContext } from "react";
import type { Card, Thread } from "./chat";
import type { Agent, Reply } from "./agent";

export interface Settings {
  provider: "" | "deepseek" | "codex" | "claude";
  model?: string;
  base_url?: string;
  has_key: boolean;
  wizard_done?: boolean;
}

export interface Turn {
  role: "user" | "assistant";
  content: string;
}

export interface AssistantApi {
  settings: () => Promise<Settings>;
  save: (s: Settings, key: string) => Promise<void>;
  test: () => Promise<{ ok: boolean; message: string }>;
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
  "": "未配置（快捷指令）",
  deepseek: "DeepSeek",
  codex: "Codex",
  claude: "Claude",
};

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
      return `[编成卡：${c.idol.name}]`;
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
    label: PROVIDER_LABEL[s.provider] + (s.provider === "deepseek" && s.model ? ` · ${s.model}` : ""),
    ask: (text, thread) => api.ask(historyOf(thread), text),
  };
}
