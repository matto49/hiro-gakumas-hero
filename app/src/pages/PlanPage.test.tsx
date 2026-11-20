import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HiroCtx } from "../lib/hiro";
import { fakeCaller } from "../test/fake";
import { PLAN } from "../test/plan";
import { PlanPage } from "./PlanPage";

afterEach(cleanup);


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

test("读进现有方案后，偶像卡框写卡名，「出方案」可以直接点（按 skin 编号重算）", async () => {
  const fake = fakeCaller({ "plan show": { ok: true, exit: 0, data: PLAN }, "plan formation": { ok: true, exit: 0, data: PLAN } });
  render(
    <HiroCtx.Provider value={fake.call}>
      <PlanPage onOpenJob={() => {}} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect((screen.getByLabelText("偶像卡") as HTMLInputElement).value).toBe("Campus mode!!"));
  const btn = screen.getByRole("button", { name: "出方案" }) as HTMLButtonElement;
  expect(btn.disabled).toBe(false);
  fireEvent.click(btn);
  await waitFor(() => expect(fake.calls.some((c) => c[1] === "formation")).toBe(true));
  expect(fake.calls.find((c) => c[1] === "formation")!.slice(0, 4)).toEqual(["plan", "formation", "--idol", "i_card-skin-shro-3-007"]);
});
