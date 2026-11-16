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
  await waitFor(() => expect(screen.getByText(/还没扫描回忆/)).toBeTruthy());
});
