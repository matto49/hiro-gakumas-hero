import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HiroCtx } from "../lib/hiro";
import { fakeCaller } from "../test/fake";
import { InfoPage } from "./InfoPage";

afterEach(cleanup);

test("支援卡页：按稀有度筛选", async () => {
  const fake = fakeCaller({
    "info supports": {
      ok: true,
      exit: 0,
      data: {
        kind: "supports",
        scanned_at: "2026-11-16 18:00:00",
        complete: true,
        items: [
          { id: "s1", name: "わたしと美鈴", rarity: "SSR", attr: "Vi", level: 60, breaks: 4 },
          { id: "s2", name: "テストSR", rarity: "SR", attr: "Da", level: 40, breaks: 1 },
        ],
      },
    },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <InfoPage kind="supports" onAsk={() => {}} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText("わたしと美鈴")).toBeTruthy());
  expect(screen.getByText("テストSR")).toBeTruthy();
  expect(screen.getByText(/上次扫描：2026-11-16 18:00/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "SR" }));
  expect(screen.queryByText("わたしと美鈴")).toBeNull();
  expect(screen.getByText("テストSR")).toBeTruthy();
});

test("没扫描过时提示去扫描", async () => {
  const fake = fakeCaller({ "info memories": { ok: true, exit: 0, data: { kind: "memories", scanned_at: null, items: [], hint: "还没扫描回忆：运行 hiro run start scan-memories" } } });
  render(
    <HiroCtx.Provider value={fake.call}>
      <InfoPage kind="memories" onAsk={() => {}} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText(/还没扫描过/)).toBeTruthy());
});

test("信息页上的「扫描」直接发起扫描任务并出确认卡", async () => {
  const fake = fakeCaller({
    "info supports": { ok: true, exit: 0, data: { kind: "supports", scanned_at: null, items: [], hint: "还没扫描" } },
    "run start scan-supports": { ok: true, exit: 3, data: {}, pending: { id: "p2", command: "run start", summary: "扫描支援卡库存（只读，不改编成）", expires_at: 1 } },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <InfoPage kind="supports" onAsk={() => {}} />
    </HiroCtx.Provider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "扫描" }));
  await waitFor(() => expect(screen.getByText("扫描支援卡库存（只读，不改编成）")).toBeTruthy());
});

test("游戏设置页按项列出 ON / OFF，带结论和「重新检查」", async () => {
  const fake = fakeCaller({
    "info game-settings": {
      ok: true,
      exit: 0,
      data: { kind: "game_settings", scanned_at: "2026-11-17 18:00:00", verdict: "设置被重置：1 项 OFF", items: [{ item: "未読早送り", state: "ON" }, { item: "支援事件剧情跳过", state: "OFF" }] },
    },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <InfoPage kind="game_settings" onAsk={() => {}} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText("支援事件剧情跳过")).toBeTruthy());
  expect(screen.getByText("OFF")).toBeTruthy();
  expect(screen.getByText("设置被重置：1 项 OFF")).toBeTruthy();
  expect(screen.getByRole("button", { name: "重新检查" })).toBeTruthy();
});

const SUPPORTS = {
  format: "gkbench.supports.v1",
  cards: {
    s1: {
      name: { ja: "わたしと美鈴", zh: "我和美铃超要好" },
      rarity: "SSR",
      type: "visual",
      lesson: { param: "visual", permil: 37, zh: "/ 概率高" },
      caps: [40, 45, 50, 55, 60],
      abilities: [
        { unlock: 1, steps: [{ lv: 40, zh: "初始视觉+52" }, { lv: 60, zh: "初始视觉+65" }] },
        { unlock: 61, steps: [{ lv: 61, zh: "还没解锁的能力" }] },
      ],
      events: [{ level: 1, zh: "来历不明的东西获得", item: "pitem_x" }],
    },
  },
};

test("支援卡页：中文名在上、日文原名在下，悬浮说明写当前等级的能力和 SP 发生率", async () => {
  const fake = fakeCaller({
    "info supports": { ok: true, exit: 0, data: { kind: "supports", scanned_at: "2026-11-16 18:00:00", items: [{ id: "s1", name: "わたしと美鈴", rarity: "SSR", attr: "Vi", level: 60, breaks: 4 }] } },
    "catalog supports": { ok: true, exit: 0, data: SUPPORTS },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <InfoPage kind="supports" onAsk={() => {}} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText("我和美铃超要好")).toBeTruthy());
  expect(screen.getByText("わたしと美鈴")).toBeTruthy();
  const row = screen.getByText("我和美铃超要好").closest("li")!;
  expect(row.textContent).toContain("视觉"); // 属性用中文
  expect(screen.getByRole("button", { name: "视觉" })).toBeTruthy(); // 筛选也是中文
  fireEvent.focus(row);
  fireEvent.pointerEnter(row);
  fireEvent.mouseEnter(row);
  await waitFor(() => expect(screen.getByText(/初始视觉\+65/)).toBeTruthy());
  expect(screen.queryByText(/初始视觉\+52/)).toBeNull(); // 只写当前等级那一档
  expect(screen.queryByText(/还没解锁的能力/)).toBeNull();
  expect(screen.getByText(/3\.7%（概率高）/)).toBeTruthy();
  expect(screen.getByText(/来历不明的东西获得/)).toBeTruthy();
  expect(screen.queryByText(/还没同步资料库/)).toBeNull();
});

test("资料库没同步时显示日文原名和同步提示，点了同步后换成中文", async () => {
  let synced = false;
  const fake = fakeCaller({
    "info supports": { ok: true, exit: 0, data: { kind: "supports", scanned_at: "2026-11-16 18:00:00", items: [{ id: "s1", name: "わたしと美鈴", rarity: "SSR", attr: "Vi", level: 60, breaks: 4 }] } },
    "catalog supports": () =>
      synced ? { ok: true, exit: 0, data: SUPPORTS } : { ok: false, exit: 1, error: { code: "catalog_missing", message: "支援卡资料还没下载", hint: "运行 hiro catalog sync" } },
    "catalog sync": () => {
      synced = true;
      return { ok: true, exit: 0, data: {} };
    },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <InfoPage kind="supports" onAsk={() => {}} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText(/还没同步资料库/)).toBeTruthy());
  expect(screen.getByText("わたしと美鈴")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "同步资料库" }));
  await waitFor(() => expect(screen.getByText("我和美铃超要好")).toBeTruthy());
  expect(screen.queryByText(/还没同步资料库/)).toBeNull();
});

test("回忆页：技能卡用中文名，能力换成中文，对不上的保留日文", async () => {
  const fake = fakeCaller({
    "info memories": {
      ok: true,
      exit: 0,
      data: { kind: "memories", scanned_at: "2026-11-16 18:00:00", items: [{ card_id: "p_card-1", card: "魅惑の視線", timing: "mid", stats: { Vo: 30, Da: 52, Vi: 79 }, abilities: ["初期ビジュアル上昇+15", "読み違い+9"] }] },
    },
    "catalog lookup": (args) => {
      const zh = args.includes("zh");
      return {
        ok: true,
        exit: 0,
        data: {
          items: { "p_card-1": { kind: "cards", id: "p_card-1", name: zh ? "魅惑视线" : "魅惑の視線", text: zh ? "好印象+3" : "好印象+3", asset: "img_x" } },
          texts: zh ? { "初期ビジュアル上昇+15": "初始视觉+15" } : {},
        },
      };
    },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <InfoPage kind="memories" onAsk={() => {}} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText("魅惑视线")).toBeTruthy());
  expect(screen.getByText("魅惑の視線")).toBeTruthy();
  expect(screen.getByText("初始视觉+15 / 読み違い+9")).toBeTruthy();
});
