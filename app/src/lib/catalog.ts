// 资料库（攻略站同步到本机的 catalog）：信息页、编成方案上的中文名、小图、悬浮说明。
// 只读本机文件（hiro catalog supports / lookup），不在打开页面时联网；没同步过就退回日文原名，页面上提示去同步。
import { useEffect, useState } from "react";
import { HiroError, data, type Caller } from "./hiro";

export interface Text2 {
  ja?: string;
  zh?: string;
}

export interface SupportAbility {
  unlock: number;
  steps: { lv: number; ja?: string; zh?: string }[];
}

export interface SupportInfo {
  name: Text2;
  rarity: string;
  type: "vocal" | "dance" | "visual" | "assist" | string;
  plan?: string;
  lesson?: { param?: string; permil?: number; ja?: string; zh?: string };
  caps?: number[];
  abilities?: SupportAbility[];
  events?: { level: number; ja?: string; zh?: string; card?: string; item?: string }[];
}

export interface Supports {
  source?: { master_date?: string };
  cards: Record<string, SupportInfo>;
}

export interface Entry {
  kind: string;
  id: string;
  name?: string | null;
  rarity?: string | null;
  plan?: string | null;
  text?: string | null;
  asset?: string | null;
}

export interface Lookup {
  items: Record<string, Entry>;
  texts: Record<string, string>;
}

export const TYPE_ZH: Record<string, string> = { vocal: "声乐", dance: "舞蹈", visual: "视觉", assist: "辅助" };
/** 属性缩写 → 中文（Vo / Da / Vi） */
export const ATTR_ZH: Record<string, string> = { Vo: "声乐", Da: "舞蹈", Vi: "视觉" };

/** 这张支援卡在 level 级时已经有的能力（每条取不超过当前等级的最高一档），中文。 */
export function activeAbilities(card: SupportInfo, level: number | null | undefined): string[] {
  if (level == null) return [];
  const out: string[] = [];
  for (const a of card.abilities ?? []) {
    if (a.unlock > level) continue;
    const step = [...a.steps].reverse().find((s) => s.lv <= level);
    const text = step?.zh ?? step?.ja;
    if (text) out.push(text);
  }
  return out;
}

/** SP 发生率「3.7%（概率高）」。 */
export function spRate(card: SupportInfo): string | null {
  const p = card.lesson?.permil;
  if (p == null) return null;
  const word = (card.lesson?.zh ?? "").replace(/^[/／\s]+/, "");
  return `${(p / 10).toFixed(1)}%${word ? `（${word}）` : ""}`;
}

// 按调用函数缓存（测试里每个用例换一个假的调用函数，互不影响）
const supportsCache = new WeakMap<Caller, Promise<Supports | null>>();

/** 本机的支援卡资料；没同步过给 null。 */
export function loadSupports(call: Caller): Promise<Supports | null> {
  let p = supportsCache.get(call);
  if (!p) {
    p = data<Supports>(call, ["catalog", "supports"]).catch((e) => {
      supportsCache.delete(call);
      if (e instanceof HiroError && e.error.code === "catalog_missing") return null;
      throw e;
    });
    supportsCache.set(call, p);
  }
  return p;
}

/** 按编号取资料（中文 + 日文各一份）、回忆能力原文换中文；没同步过 missing=true。 */
export async function lookup(call: Caller, ids: string[], texts: string[] = []): Promise<{ zh: Lookup; ja: Lookup; missing: boolean }> {
  const empty: Lookup = { items: {}, texts: {} };
  const uniq = [...new Set(ids.filter(Boolean))];
  const ts = [...new Set(texts.filter(Boolean))];
  if (!uniq.length && !ts.length) return { zh: empty, ja: empty, missing: false };
  const args = (lang: string) => ["catalog", "lookup", "--ids", uniq.join(","), ...(ts.length ? ["--texts", ts.join("\n")] : []), "--lang", lang];
  try {
    const [zh, ja] = await Promise.all([data<Lookup>(call, args("zh")), data<Lookup>(call, args("ja"))]);
    return { zh, ja, missing: false };
  } catch (e) {
    if (e instanceof HiroError && e.error.code === "catalog_missing") return { zh: empty, ja: empty, missing: true };
    throw e;
  }
}

/** React 里用：ids / texts 变了重查。 */
export function useLookup(call: Caller, ids: string[], texts: string[] = []) {
  const key = ids.join(",") + "\u0000" + texts.join("\n");
  const [state, setState] = useState<{ zh: Lookup; ja: Lookup; missing: boolean } | null>(null);
  useEffect(() => {
    let live = true;
    lookup(call, ids, texts).then(
      (r) => live && setState(r),
      () => live && setState(null),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [call, key]);
  return state;
}
