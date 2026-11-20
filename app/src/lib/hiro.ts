// 调工具层：桌面程序里走 Go 绑定（Hiro.call），信封原样带回。组件通过 HiroCtx 拿调用函数，测试里换成假的。
import { createContext, useContext } from "react";
import type { CliError, Pending, Result } from "../mygo";

export type Envelope = Result;
export type { CliError, Pending };
export type Caller = (args: string[]) => Promise<Envelope>;

const realCaller: Caller = async (args) => (await import("../mygo")).Hiro.call(args);

export const HiroCtx = createContext<Caller>(realCaller);

export function useHiro(): Caller {
  return useContext(HiroCtx);
}

/** HIRO_HOME 里的图（Go 侧 homeFiles）：Mac / Linux 走 hiro://localhost/，Windows 的 WebView2 是 http://hiro.localhost/。 */
export function homeUrl(rel: string): string {
  const win = typeof navigator !== "undefined" && /Windows/.test(navigator.userAgent);
  return (win ? "http://hiro.localhost/" : "hiro://localhost/") + rel.replace(/^\/+/, "");
}

export function spriteUrl(asset: string): string {
  return homeUrl(`sprites/${asset}.png`);
}

/** 事件里的截图是绝对路径；只取 traces/ 之后那段交给 homeFiles。 */
export function frameUrl(abs: string | null | undefined): string | null {
  if (!abs) return null;
  const m = abs.replace(/\\/g, "/").match(/\/traces\/([^/]+\/frames\/[0-9]+\.jpg)$/);
  return m ? homeUrl(`traces/${m[1]}`) : null;
}

// ---- 工具层返回的数据形状 ----

export interface Job {
  id: string;
  kind: string;
  params: Record<string, unknown>;
  state: "running" | "paused" | "takeover" | "stopping" | "done" | "failed" | "stopped" | "lost";
  summary: string;
  started_at: number;
  ended_at: number | null;
  exit_code: number | null;
  /** 主循环认暂停旗标（对局）；日常、扫描只能停止 */
  pausable?: boolean;
}

export interface JobEvent {
  seq: number;
  t: number;
  type: string;
  text: string;
  step?: number;
  page?: string | null;
  action?: string | null;
  source?: string | null;
  frame?: string | null;
  notes?: string[];
}

export interface Listing<T = Record<string, unknown>> {
  kind: string;
  scanned_at: string | null;
  complete?: boolean | null;
  items: T[];
  hint?: string;
}

export interface Summary {
  account: { plv?: number; ap?: number; ap_max?: number; jewel?: number } | null;
  kinds: Record<string, { count: number; scanned_at: string | null }>;
}

export const LIVE_STATES = new Set(["running", "paused", "takeover", "stopping"]);

/** 调用并取 data；失败抛出带 code / hint 的错误。 */
export class HiroError extends Error {
  constructor(public error: CliError) {
    super(error.message);
  }
}

export async function data<T>(call: Caller, args: string[]): Promise<T> {
  const r = await call(args);
  if (!r.ok) throw new HiroError(r.error ?? { code: "error", message: "出错了" });
  return r.data as T;
}
