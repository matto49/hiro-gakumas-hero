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
  hajime: "初",
  nia: "N.I.A",
  tower: "偶像之路",
  daily: "日常",
  "scan-idols": "扫描偶像卡",
  "scan-supports": "扫描支援卡",
  "scan-memories": "扫描回忆",
  "scan-account": "读首页数值",
  "check-settings": "检查游戏设置",
  "write-formation": "写编成",
  "fit-window": "调窗口大小",
  selftest: "自检",
};

export const ATTR_STYLE: Record<string, string> = {
  Vo: "bg-vo-t text-vo-strong",
  Da: "bg-da-t text-da-strong",
  Vi: "bg-vi-t text-vi-ink",
};

export const TIMING_LABEL: Record<string, string> = { start: "开局", mid: "中间", final: "最终" };

/** 页面名（trace 里的场景名，hiro/contracts/observation.py FIELD_CATALOG）→ 中文。没收录的原样显示。 */
export const PAGE_LABEL: Record<string, string> = {
  HOME: "首页",
  SELFTEST: "自检",
  ACTION_SELECT: "选行动",
  HIF_EXAM: "考试中",
  HIF_EXAM_BRIEFING: "考试说明",
  HIF_EXAM_RESULT: "考试结果",
  HIF_FINALS_START: "本战开始",
  HIF_FINALS_RANKING: "本战排名",
  HIF_ROUND_INTERVAL: "轮间",
  SELECT_CARD: "选技能卡",
  SELECT_DRINK: "选饮料",
  SELECT_PITEM: "选 P 道具",
  PRODUCE_START_CONFIRM: "开局确认",
  PRODUCE_END: "培育结束",
  PRODUCE_LOG: "培育记录",
  CONSULT: "相談",
  STUDY: "授業",
  OUTING: "外出",
  EVENT_CHOICE: "事件选项",
  DECK_LIST: "牌组",
  SKILL_CARD_REMOVAL: "删卡",
  SKILL_CARD_ENHANCE: "强化卡",
  SKILL_CARD_CHANGE_1: "换卡",
  SKILL_CARD_CHANGE_2: "换卡",
  PDRINK_MAX: "饮料满了",
  PDRINK_MAX_CONFIRM: "饮料满了",
  SPECIAL_GUIDANCE: "特別指導",
  PITEM_CUSTOMIZE: "P 道具定制",
  PITEM_CUSTOMIZE_DONE: "P 道具定制",
  CUSTOMIZE_RESULT: "定制结果",
  UPGRADE_RESULT: "强化结果",
  DELETE_RESULT: "删卡结果",
  DIALOG: "弹窗",
  ACQUIRE: "获得",
  STAR_GAIN: "获得星",
  MENU: "菜单",
  TRANSITION: "切换中",
  GIFT: "礼物",
  commu: "剧情",
  unknown: "没认出的画面",
  TOWER_LAYERS: "偶像之路 · 选层",
  TOWER_FORMATION: "偶像之路 · 编成",
};

export function pageLabel(p: string | null | undefined): string {
  return p ? (PAGE_LABEL[p] ?? p) : "—";
}

/** 动作名（hiro/contracts/action.py ActionKind）→ 中文。 */
export const ACTION_LABEL: Record<string, string> = {
  ProduceNext: "选行动",
  ActivateEffect: "选取",
  ExcludeCard: "不拿",
  Reroll: "刷新候选",
  ShopBuy: "买",
  ShopUpgradeCard: "强化",
  ShopEnd: "离开商店",
  EventSelect: "选选项",
  PresentReceive: "领取",
  IntervalBuy: "轮间购买",
  IntervalUpgradeCard: "轮间强化",
  IntervalChangeCard: "轮间换卡",
  IntervalEnd: "结束轮间",
  CustomizeCard: "定制",
  CustomizeEnd: "结束定制",
  SelectFormation: "选编成",
  ChangeCard: "换卡",
  RemoveCard: "删卡",
  EnhanceCard: "强化卡",
  Dialog: "处理弹窗",
  SelectQualifyMemory: "选回忆",
  DiscardDrink: "丢饮料",
  PlayCard: "出牌",
  UseDrink: "喝饮料",
  Skip: "跳过",
  Continue: "继续",
  Wait: "等待",
  Recover: "恢复",
  Noop: "不动",
  Lesson: "上课",
  Pick: "选择",
};

export function actionLabel(a: string | null | undefined): string {
  return a ? (ACTION_LABEL[a] ?? a) : "";
}

export function clock(t: number): string {
  const d = new Date(t * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
