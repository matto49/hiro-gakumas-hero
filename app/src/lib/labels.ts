// 界面上的叫法。

/** 每一步是谁定的（设计稿 §14 签名细节）：规则、算法、LLM、兜底。 */
export function sourceLabel(source: string | null | undefined): string {
  const s = (source ?? "").toLowerCase();
  if (!s) return "自动";
  if (/llm|claude|codex|openai|deepseek/.test(s)) return "LLM";
  if (/rescue|fallback|兜底/.test(s)) return "兜底";
  if (/bench|search|gnrpa|uinet|sim|algo|engine/.test(s)) return "算法";
  if (/rule|community|default|plan/.test(s)) return "规则";
  return source!;
}

export const STATE_LABEL: Record<string, string> = {
  running: "游玩中",
  paused: "已暂停",
  takeover: "你在接手",
  stopping: "正在停下",
  done: "完成",
  failed: "出错停下",
  stopped: "已停止",
  lost: "中断（程序重启）",
};

export const KIND_LABEL: Record<string, string> = {
  hif: "H.I.F",
  tower: "偶像之路",
  daily: "日常",
  "scan-supports": "扫描支援卡",
  "scan-memories": "扫描回忆",
  selftest: "自检",
};

export const ATTR_STYLE: Record<string, string> = {
  Vo: "bg-vo-t text-vo",
  Da: "bg-da-t text-da",
  Vi: "bg-vi-t text-vi",
};

export const TIMING_LABEL: Record<string, string> = { start: "开局", mid: "中间", final: "最终" };

/** 页面名（trace 里的英文）→ 中文。没收录的原样显示。 */
export const PAGE_LABEL: Record<string, string> = {
  HIF_EXAM: "考试中",
  ACTION_SELECT: "选行动",
  DECK_LIST: "牌组",
  TOWER_LAYERS: "偶像之路 · 选层",
  TOWER_FORMATION: "偶像之路 · 编成",
  HOME: "首页",
  SELFTEST: "自检",
};

export function pageLabel(p: string | null | undefined): string {
  return p ? (PAGE_LABEL[p] ?? p) : "—";
}

export function clock(t: number): string {
  const d = new Date(t * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
