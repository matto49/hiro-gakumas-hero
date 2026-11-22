// 界面预览（不打包进程序）：假数据 + 指定页面，给离屏截图和设计验收用。
//   bun run preview:build  →  .preview/preview/index.html#start（chat / start / cockpit / plan / supports / memories / settings）
import { StrictMode } from "react";
import CATALOG from "./preview-catalog.json";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { AssistantCtx, type AssistantApi } from "./lib/assistant";
import { BootCtx, type BootApi } from "./lib/bootstrap";
import type { Thread } from "./lib/chat";
import { HiroCtx, type Envelope } from "./lib/hiro";
import type { View } from "./components/Sidebar";
import "./style.css";

// 离屏 WebKit 会把页面当成隐藏，轮询就停了；预览里固定成可见。
Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });

// 资料库的一小份真数据（预览里的中文名、悬浮说明用；从初星攻略站同步的资料里截的）
const CAT = CATALOG as unknown as {
  supports: Record<string, { name: { ja: string; zh?: string } }>;
  cards: Record<string, { id: string; ja: string; name: string; text: string; asset: string; rarity: string }>;
  texts: Record<string, string>;
};
const now = Date.now() / 1000;
const ok = (data: unknown): Envelope => ({ ok: true, exit: 0, data });
const SUP = [
  ["わたしと美鈴、超仲良し", "Vi", 60, 4], ["さあ、もう一戦！", "Vo", 60, 4], ["そろそろ焼けたかな？", "Da", 60, 4],
  ["二人ならあっという間だね", "Vo", 60, 4], ["あっちも行きたいですわ！", "Da", 60, 4], ["さみだれ", "Vi", 60, 4],
  ["まだ上がりませんように！", "Vo", 55, 3], ["ひとりごとです", "Vi", 55, 3], ["会長、準備は万端です", "Vi", 50, 2],
].map(([name, attr, level, breaks], i) => ({
  id: Object.entries(CAT.supports).find(([, v]) => v.name.ja === name)?.[0] ?? `s${i}`, name, rarity: "SSR", attr, level, breaks,
}));
const MEM = [
  ["ワクワクが止まらない", "start", 30, 52, 79], ["デイドリーミング", "start", 41, 60, 66], ["届いて！", "mid", 55, 70, 40], ["私がスター", "mid", 60, 22, 71],
].map(([card, timing, Vo, Da, Vi]) => ({
  card, card_id: Object.values(CAT.cards).find((c) => c.ja === card)?.id ?? null, timing, stats: { Vo, Da, Vi },
  abilities: ["初期ビジュアル上昇+15", "ボーカルレッスン終了時、45%の確率で体力回復4"],
}));
const JOB = { id: "J1", kind: "hif", params: {}, state: "running", summary: "开一局 H.I.F 选拔（【Campus mode!!】篠澤広）", started_at: now - 900, ended_at: null, exit_code: null, pausable: true };
const EVENTS = [
  { seq: 0, t: now - 900, type: "start", text: JOB.summary },
  { seq: 1, t: now - 600, type: "step", text: "Da 离下一档评价最近，这次课收益最高", page: "ACTION_SELECT", action: "ProduceNext", source: "llm" },
  { seq: 2, t: now - 420, type: "step", text: "按攻略的定制优先级，花うさぎ排在羽うさぎ前面", page: "SELECT_PITEM", action: "ActivateEffect", source: "rule" },
  { seq: 3, t: now - 120, type: "step", text: "终局分预测中位 368,997：先出エウレカ！叠好調", page: "HIF_EXAM", action: "PlayCard", source: "bench" },
  { seq: 4, t: now - 30, type: "step", text: "最后一回合把元気全部兑现成分数", page: "HIF_EXAM", action: "PlayCard", source: "search" },
];
const PLAN = {
  created: "2026-11-20 10:20:00", idol: { id: "i_card-shro-3-007", name: "Campus mode!!" }, main_attr: "Da",
  support: { set: 4, value: 199, counts: { Vo: 0, Da: 4, Vi: 2 }, own: SUP.slice(0, 5).map((s) => ({ ...s, why: ["事件 Da+20"] })), rental: { name: "あなたにも作ってあげる！", attr: "Vi", value: 20, estimated: true } },
  memory: { set: 5, chosen: MEM.map((m, i) => ({ name: m.card, upgrade: i < 2 ? 1 : 0, timing: m.timing, role: ["核心（やる気引擎）", "核心（持续）", "兑现", "兑现辅助"][i] })) },
  notes: ["4 张集中在 Da，其余属性靠课程和考试补：稳妥线 一属性 2000 / 二属性 1400 / 三属性 1100 [ep08]"],
};

const routes: [string, Envelope][] = [
  ["run status", ok({ current: JOB, recent: [] })],
  ["info summary", ok({ account: { plv: 65, ap: 85, ap_max: 100, jewel: 15220 }, kinds: { supports: { count: 67, scanned_at: "2026-11-17 02:23:05" }, memories: { count: 384, scanned_at: "2026-11-16 18:44:59" }, account: { count: 1, scanned_at: "2026-11-20 10:01:00" } } })],
  ["info supports", ok({ kind: "supports", scanned_at: "2026-11-17 02:23:05", complete: true, items: SUP })],
  ["catalog supports", ok({ format: "gkbench.supports.v1", cards: CAT.supports })],
  ["info memories", ok({ kind: "memories", scanned_at: "2026-11-16 18:44:59", complete: true, items: MEM })],
  ["plan show", ok(PLAN)],
  ["doctor", ok({ items: [
    { id: "python", title: "运行环境", ok: true, detail: "Python 3.10.14，依赖齐全", next: "", optional: false },
    { id: "catalog", title: "资料库", ok: null, detail: "还没下载", next: "点「下载资料库」", optional: false },
    { id: "platform", title: "游戏", ok: false, detail: "连不上 maaTools（端口 18080）", next: "在 PlayCover 的游戏设置里打开 maaTools", optional: false },
  ] })],
  ["secret list", ok({ secrets: [{ name: "game-account" }] })],
];
const call = async (args: string[]): Promise<Envelope> => {
  const k = args.join(" ");
  if (k.startsWith("catalog lookup")) {
    const zh = args.includes("zh");
    const items = Object.fromEntries(Object.values(CAT.cards).map((c) => [c.id, { ...c, name: zh ? c.name : c.ja }]));
    return ok({ items, texts: zh ? CAT.texts : {} });
  }
  if (k.startsWith("run events")) {
    const since = Number(args[args.indexOf("--since") + 1] ?? 0) || 0;
    return ok({ job: "J1", state: "running", events: EVENTS.filter((e) => e.seq >= since) });
  }
  return routes.find(([p]) => k.startsWith(p))?.[1] ?? { ok: false, exit: 1, error: { code: "preview", message: `预览里没有：${k}` } };
};

const THREAD: Thread = {
  id: "t1", title: "我有多少张 SSR 支援卡？", updatedAt: Date.now(),
  messages: [
    { id: "1", role: "user", text: "我有多少张 SSR 支援卡？Vo、Da、Vi 各几张？", cards: [], at: 0 },
    { id: "2", role: "agent", text: "按 2026-11-17 02:23 那次扫描，你一共有 **45 张** SSR 支援卡：\n\n- **Vo**：19 张\n- **Vi**：16 张\n- **Da**：8 张\n- Assist（HP）：2 张", cards: [{ type: "info", title: "SSR 支援卡", kind: "supports", items: SUP, scannedAt: "2026-11-17 02:23:05" }], at: 0 },
    { id: "3", role: "user", text: "帮我开一局 H.I.F 选拔，用篠澤広", cards: [], at: 0 },
    { id: "4", role: "agent", text: "编成按方案来，开局要你点一下：", cards: [{ type: "confirm", pending: { id: "p1", command: "run start", summary: "开一局 H.I.F 选拔（【Campus mode!!】篠澤広）", expires_at: now + 600 }, status: "confirmed" }, { type: "run", jobId: "J1" }], at: 0 },
    { id: "5", role: "user", text: "打完以后领一下日常", cards: [], at: 0 },
    { id: "6", role: "agent", text: "这局还在跑，跑完才能开下一个任务。先把日常排上，你点了才开始：", cards: [{ type: "confirm", pending: { id: "p2", command: "run start", summary: "领日常：活動費、礼物、工作、竞赛", expires_at: now + 600 }, status: "waiting" }], at: 0 },
  ],
};
const api: AssistantApi = {
  settings: async () => ({ provider: "deepseek", model: "deepseek-flash", has_key: true, wizard_done: location.hash !== "#wizard" }),
  save: async () => {}, test: async () => ({ ok: true, message: "模型回答：可以" }), models: async () => ["deepseek-flash", "deepseek-v4-pro"],
  ask: async () => ({ cards: [] }), listThreads: async () => [THREAD], saveThread: async () => {}, deleteThread: async () => {},
};

// #supports-tip / #memories-tip：打开第一行的悬浮说明（截图核对用）
const [page = "chat", tipFlag] = (location.hash.slice(1) || "chat").split("-");
if (tipFlag === "tip") {
  const poke = (n = 0) => {
    const row = document.querySelector("main li");
    if (!row) return n < 50 && setTimeout(() => poke(n + 1), 100);
    for (const t of ["pointerenter", "mouseenter", "pointerover", "mouseover"]) row.dispatchEvent(new PointerEvent(t, { bubbles: t.endsWith("over") }));
    row.dispatchEvent(new FocusEvent("focus"));
  };
  setTimeout(poke, 300);
}
const boot: BootApi = { status: async () => ({ ready: true, steps: [] }), reset: async () => {}, run: async () => {} };
const VIEWS: Record<string, View> = {
  chat: { name: "chat" }, start: { name: "start" }, cockpit: { name: "cockpit", jobId: "J1" }, plan: { name: "plan" },
  supports: { name: "info", kind: "supports" }, memories: { name: "info", kind: "memories" }, settings: { name: "settings" },
};
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HiroCtx.Provider value={call}>
      <AssistantCtx.Provider value={api}>
        <BootCtx.Provider value={boot}>
          <App initialView={VIEWS[page]} />
        </BootCtx.Provider>
      </AssistantCtx.Provider>
    </HiroCtx.Provider>
  </StrictMode>,
);
