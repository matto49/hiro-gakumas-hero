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
  const { AssistantCtx } = await import("../lib/assistant");
  const saved: unknown[] = [];
  const api = {
    settings: async () => ({ provider: "deepseek" as const, has_key: false, wizard_done: true }),
    save: async () => {},
    test: async () => ({ ok: true, message: "" }),
    ask: async () => ({ cards: [] }),
    listThreads: async () => [],
    saveThread: async (t: unknown) => void saved.push(t),
    deleteThread: async () => {},
  };
  render(
    <HiroCtx.Provider value={fake.call}>
      <AssistantCtx.Provider value={api}>
        <App />
      </AssistantCtx.Provider>
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText(/DeepSeek（还没填 Key）/)).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "帮我领日常奖励" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "开始" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "开始" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "打开这局的驾驶舱" })).toBeTruthy());
  expect(fake.calls.some((c) => c.join(" ") === "confirm p9")).toBe(true);
  await waitFor(() => expect(saved.length).toBeGreaterThan(0));
});

test("扫描任务的对局卡写出信息库更新了几条", async () => {
  const { RunCard } = await import("./Cards");
  const fake = fakeCaller({
    "run events": {
      ok: true,
      exit: 0,
      data: {
        job: "j1",
        state: "done",
        events: [
          { seq: 0, t: 1, type: "start", text: "扫描支援卡库存（只读，不改编成）" },
          { seq: 1, t: 2, type: "imported", text: "已更新信息库：67 条" },
          { seq: 2, t: 3, type: "done", text: "完成" },
        ],
      },
    },
    "run status": { ok: true, exit: 0, data: { current: null, recent: [] } },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <RunCard card={{ type: "run", jobId: "j1" }} onOpen={() => {}} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText("已更新信息库：67 条")).toBeTruthy());
});

test("编成卡列出支援卡和回忆", async () => {
  const { FormationCard } = await import("./Cards");
  const { PLAN } = await import("../test/plan");
  render(<FormationCard card={{ type: "formation", plan: PLAN }} />);
  expect(screen.getByText("そろそろ焼けたかな？")).toBeTruthy();
  expect(screen.getByText(/ワクワクが止まらない\+/)).toBeTruthy();
  expect(screen.getByText(/主属性 Da/)).toBeTruthy();
});

test("任务结束后重新读账号概况", async () => {
  const { App } = await import("../App");
  const { AssistantCtx } = await import("../lib/assistant");
  let n = 0;
  const fake = fakeCaller({
    "run status": () => {
      n += 1;
      return { ok: true, exit: 0, data: { current: n === 1 ? { id: "j1", state: "running", summary: "读首页" } : null, recent: [] } };
    },
    "info summary": { ok: true, exit: 0, data: { account: null, kinds: {} } },
  });
  const api = {
    settings: async () => ({ provider: "deepseek" as const, has_key: false, wizard_done: true }),
    save: async () => {},
    test: async () => ({ ok: true, message: "" }),
    ask: async () => ({ cards: [] }),
    listThreads: async () => [],
    saveThread: async () => {},
    deleteThread: async () => {},
  };
  render(
    <HiroCtx.Provider value={fake.call}>
      <AssistantCtx.Provider value={api}>
        <App />
      </AssistantCtx.Provider>
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(fake.calls.filter((c) => c.join(" ") === "info summary").length).toBeGreaterThan(1), { timeout: 6000 });
});

test("游戏设置的信息卡按项显示 ON / OFF", async () => {
  const { InfoCard } = await import("./Cards");
  render(<InfoCard card={{ type: "info", title: "游戏设置", kind: "game_settings", items: [{ item: "支援事件剧情跳过", state: "OFF" }] }} />);
  expect(screen.getByText("支援事件剧情跳过")).toBeTruthy();
  expect(screen.getByText("OFF")).toBeTruthy();
});
