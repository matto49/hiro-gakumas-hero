import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HiroCtx } from "../lib/hiro";
import { fakeCaller } from "../test/fake";
import { PlanPage } from "./PlanPage";

afterEach(cleanup);

export const PLAN = {
  created: "2026-11-17 16:00:00",
  idol: { id: "i_card-shro-3-007", name: "Campus mode!!", plan: "ProducePlanType_Plan2", profile: "yaruki" },
  main_attr: "Da",
  support: {
    set: 4, value: 199, counts: { Vo: 0, Da: 4, Vi: 2 },
    own: [{ id: "s1", name: "そろそろ焼けたかな？", attr: "Da", level: 60, value: 20, why: ["事件 Da+20"] }],
    rental: { name: "あなたにも作ってあげる！", attr: "Vi", value: 20, estimated: true },
  },
  memory: { set: 5, cards: [], chosen: [{ id: "p13", card_id: "c1", name: "ワクワクが止まらない", upgrade: 1, timing: "start", slot: "A", role: "核心" }] },
  notes: ["4 张集中在 Da"],
  path: "/h/formation-plan.json",
};

test("显示现有方案，点「看差在哪」发起只看不改的写编成任务", async () => {
  const fake = fakeCaller({
    "plan show": { ok: true, exit: 0, data: PLAN },
    "run start write-formation": (args) => ({ ok: true, exit: 3, data: {}, pending: { id: "p1", command: "run start", summary: args.includes("--dry-run") ? "看差在哪几格（只看，不改）" : "写进游戏", expires_at: 1 } }),
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <PlanPage onOpenJob={() => {}} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText("そろそろ焼けたかな？")).toBeTruthy());
  expect(screen.getByText(/ワクワクが止まらない/)).toBeTruthy();
  expect(screen.getByText(/租借：/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "看差在哪（不改）" }));
  await waitFor(() => expect(screen.getByText("看差在哪几格（只看，不改）")).toBeTruthy());
  expect(fake.calls.some((c) => c.join(" ") === "run start write-formation --scenario hif --dry-run")).toBe(true);
});

test("出方案：填偶像卡编号和编成号后调用 plan formation", async () => {
  const fake = fakeCaller({
    "plan show": { ok: false, exit: 1, error: { code: "no_plan", message: "还没有编成方案", hint: "先出一份" } },
    "plan formation": { ok: true, exit: 0, data: PLAN },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <PlanPage onOpenJob={() => {}} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText(/还没有编成方案/)).toBeTruthy());
  fireEvent.change(screen.getByLabelText("偶像卡"), { target: { value: "i_card-skin-shro-3-007" } });
  fireEvent.change(screen.getByLabelText("支援卡编成号"), { target: { value: "4" } });
  fireEvent.change(screen.getByLabelText("回忆编成号"), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "出方案" }));
  await waitFor(() => expect(screen.getByText("そろそろ焼けたかな？")).toBeTruthy());
  expect(fake.calls.find((c) => c[0] === "plan" && c[1] === "formation")).toEqual(["plan", "formation", "--idol", "i_card-skin-shro-3-007", "--support-set", "4", "--memory-set", "5"]);
});
