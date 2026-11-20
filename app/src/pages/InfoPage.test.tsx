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
