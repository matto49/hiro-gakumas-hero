// 模型设置和对话后端（Go 侧 Assistant / Threads）。组件经 AssistantCtx 拿，测试里换成假的。
import { createContext, useContext } from "react";
import type { Card, Thread } from "./chat";
import type { Agent, Reply } from "./agent";
import { messages as t } from "../locales";

/** OpenAI 兼容的 API 服务商（Go 侧 Presets，顺序即界面顺序）。 */
export const API_PROVIDERS = ["deepseek", "kimi", "qwen", "glm", "openai", "openrouter", "custom"] as const;
export type ApiProvider = (typeof API_PROVIDERS)[number];
/** 本机命令行（设置页「高级」里）。 */
export const CLI_PROVIDERS = ["claude", "codex"] as const;
export type Provider = ApiProvider | (typeof CLI_PROVIDERS)[number] | "none";

export interface Settings {
  /** deepseek 是缺省；none = 不用模型 */
  provider: Provider;
  model?: string;
  /** 自定义服务商的地址 */
  base_url?: string;
  /** 当前服务商有没有存 Key */
  has_key: boolean;
  /** 每家服务商有没有存 Key */
  has_keys?: Partial<Record<ApiProvider, boolean>>;
  wizard_done?: boolean;
}

export interface Preset {
  id: ApiProvider;
  base_url: string;
  default_model: string;
  key_url: string;
}

export const DEFAULT_DEEPSEEK_MODEL = "deepseek-flash";

export function isApi(p: Provider): p is ApiProvider {
  return (API_PROVIDERS as readonly string[]).includes(p);
}

export function isCli(p: Provider): boolean {
  return (CLI_PROVIDERS as readonly string[]).includes(p);
}

export interface Turn {
  role: "user" | "assistant";
  content: string;
}

export interface AssistantApi {
  settings: () => Promise<Settings>;
  save: (s: Settings, key: string) => Promise<void>;
  test: () => Promise<{ ok: boolean; message: string }>;
  /** 这家服务商这个 Key 能用的模型（key 空 = 用已存的；没 Key 或连不上时抛错） */
  models?: (provider: ApiProvider, baseURL: string, key: string) => Promise<string[]>;
  /** 服务商预设（地址、缺省模型、创建 Key 的地址） */
  providers?: () => Promise<Preset[]>;
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
  models: async (p, url, key) => ((await (await mygo()).Assistant.models(p, url, key)) as string[]) ?? [],
  providers: async () => ((await (await mygo()).Assistant.providers()) as Preset[]) ?? [],
  ask: async (h, t) => (await (await mygo()).Assistant.ask(h, t)) as unknown as Reply,
  listThreads: async () => ((await (await mygo()).Threads.list()) as unknown as Thread[]) ?? [],
  saveThread: async (t) => (await mygo()).Threads.save(t.id, t as never),
  deleteThread: async (id) => (await mygo()).Threads.delete(id),
};

export const AssistantCtx = createContext<AssistantApi>(realAssistant);

export function useAssistant(): AssistantApi {
  return useContext(AssistantCtx);
}

export function providerLabel(p: Provider): string {
  switch (p) {
    case "deepseek": return t.providerDeepseek();
    case "kimi": return t.providerKimi();
    case "qwen": return t.providerQwen();
    case "glm": return t.providerGlm();
    case "openai": return t.providerOpenai();
    case "openrouter": return t.providerOpenrouter();
    case "custom": return t.providerCustom();
    case "claude": return t.providerClaude();
    case "codex": return t.providerCodex();
    case "none": return t.providerNone();
  }
}

/** 这个模型设置能不能对话（API 服务商要有 Key 和模型；命令行直接能用）。 */
export function modelReady(s: Settings | null): boolean {
  if (!s) return false;
  if (isCli(s.provider)) return true;
  return isApi(s.provider) && s.has_key && !!(s.model || s.provider === "deepseek");
}

/** 侧栏上的模型名：没法用的时候写清楚为什么只能点快捷指令。 */
export function modelLabel(s: Settings | null): string {
  if (!s) return "…";
  if (s.provider === "none") return t.modelLabelNone();
  const name = providerLabel(s.provider);
  if (isApi(s.provider)) {
    if (!s.has_key) return t.modelLabelNoKey(name);
    const model = s.model || (s.provider === "deepseek" ? DEFAULT_DEEPSEEK_MODEL : "");
    return model ? `${name} · ${model}` : t.modelLabelNoModel(name);
  }
  return name;
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
