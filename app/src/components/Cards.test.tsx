import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HiroCtx } from "../lib/hiro";
import { fakeCaller } from "../test/fake";
import { ConfirmCard, ErrorCard } from "./Cards";

afterEach(cleanup);

test("确认卡点「开始」才调用 confirm", async () => {
  const fake = fakeCaller({ "confirm abc": { ok: true, exit: 0, data: { job: { id: "j1", state: "running" } } } });
  let done: unknown = null;
  render(
    <HiroCtx.Provider value={fake.call}>
      <ConfirmCard
        card={{ type: "confirm", pending: { id: "abc", command: "run start", summary: "开一局 H.I.F 选拔", expires_at: 1 }, status: "waiting" }}
        onResolved={(r) => (done = r)}
      />
    </HiroCtx.Provider>,
  );
  expect(screen.getByText("开一局 H.I.F 选拔")).toBeTruthy();
  expect(fake.calls).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "开始" }));
  await waitFor(() => expect(fake.calls).toEqual([["confirm", "abc"]]));
  await waitFor(() => expect(done).not.toBeNull());
});

test("确认卡点「先不」调用 cancel", async () => {
  const fake = fakeCaller({ "cancel abc": { ok: true, exit: 0, data: { cancelled: true } } });
  render(
    <HiroCtx.Provider value={fake.call}>
      <ConfirmCard card={{ type: "confirm", pending: { id: "abc", command: "run start", summary: "x", expires_at: 1 }, status: "waiting" }} onResolved={() => {}} />
    </HiroCtx.Provider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "先不" }));
  await waitFor(() => expect(fake.calls).toEqual([["cancel", "abc"]]));
});

test("出错卡用白话写原因和下一步", () => {
  render(<ErrorCard card={{ type: "error", error: { code: "busy", message: "已经有任务在跑：领日常奖励", hint: "等它结束，或停止它" } }} />);
  expect(screen.getByText("已经有任务在跑：领日常奖励")).toBeTruthy();
  expect(screen.getByText("等它结束，或停止它")).toBeTruthy();
});

test("对话：点快捷指令 → 确认卡 → 点开始后出对局卡", async () => {
  const { App } = await import("../App");
  const fake = fakeCaller({
    "run status": { ok: true, exit: 0, data: { current: null, recent: [] } },
    "info summary": { ok: true, exit: 0, data: { account: null, kinds: {} } },
    "run start daily": { ok: true, exit: 3, data: {}, pending: { id: "p9", command: "run start", summary: "领日常奖励", expires_at: 1 } },
    "confirm p9": { ok: true, exit: 0, data: { job: { id: "j9", state: "running", summary: "领日常奖励" } } },
    "run events": { ok: true, exit: 0, data: { job: "j9", state: "running", events: [] } },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <App />
    </HiroCtx.Provider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "帮我领日常奖励" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "开始" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "开始" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "打开这局的驾驶舱" })).toBeTruthy());
  expect(fake.calls.some((c) => c.join(" ") === "confirm p9")).toBe(true);
});
