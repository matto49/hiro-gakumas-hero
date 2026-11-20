import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HiroCtx } from "../lib/hiro";
import { fakeCaller } from "../test/fake";
import { Cockpit } from "./Cockpit";

afterEach(cleanup);

function setup(pausable: boolean) {
  const job = { id: "j1", kind: pausable ? "hif" : "daily", params: {}, state: "running", summary: "x", started_at: 1, ended_at: null, exit_code: null, pausable };
  const fake = fakeCaller({
    "run events": { ok: true, exit: 0, data: { job: "j1", state: "running", events: [] } },
    "run status": { ok: true, exit: 0, data: { current: job, recent: [] } },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <Cockpit jobId="j1" onBack={() => {}} />
    </HiroCtx.Provider>,
  );
}

test("不能暂停的任务（日常、扫描）只给「停止」", async () => {
  setup(false);
  await waitFor(() => expect(screen.getByRole("button", { name: "停止" })).toBeTruthy());
  expect(screen.queryByRole("button", { name: "暂停" })).toBeNull();
  expect(screen.queryByRole("button", { name: "我来接手" })).toBeNull();
});

test("对局可以暂停和接手", async () => {
  setup(true);
  await waitFor(() => expect(screen.getByRole("button", { name: "我来接手" })).toBeTruthy());
  expect(screen.getByRole("button", { name: "暂停" })).toBeTruthy();
});

test("结束后显示每场分数，点「看复盘」生成复盘页并打开", async () => {
  const job = { id: "j1", kind: "hif", params: {}, state: "done", summary: "开一局 H.I.F 选拔", started_at: 1, ended_at: 2, exit_code: 0, pausable: true };
  const opened: string[] = [];
  const orig = window.open;
  window.open = ((u: string) => (opened.push(String(u)), null)) as typeof window.open;
  const fake = fakeCaller({
    "run events": {
      ok: true,
      exit: 0,
      data: { job: "j1", state: "done", events: [{ seq: 0, t: 1, type: "report", text: "3 场考试：12,271 / 85,055 / 368,997", exams: [{ label: "qualify-1", score: 12271 }, { label: "qualify-2", score: 85055 }, { label: "qualify-3", score: 368997 }] }, { seq: 1, t: 2, type: "done", text: "完成" }] },
    },
    "run status": { ok: true, exit: 0, data: { current: null, recent: [job] } },
    "run review": { ok: true, exit: 0, data: { job: "j1", path: "/h/jobs/j1/review.html", url: "review/j1.html" } },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <Cockpit jobId="j1" onBack={() => {}} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText("368,997")).toBeTruthy());
  expect(screen.getByText("12,271")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "看复盘" }));
  await waitFor(() => expect(opened.length).toBe(1));
  expect(opened[0]).toContain("review/j1.html");
  expect(fake.calls.some((c) => c.join(" ") === "run review --job j1")).toBe(true);
  window.open = orig;
});

test("每一步用中文页面名和动作名；事件重复返回时不重复显示", async () => {
  const job = { id: "j1", kind: "hif", params: {}, state: "running", summary: "x", started_at: 1, ended_at: null, exit_code: null, pausable: true };
  const fake = fakeCaller({
    // 故意不管 --since，每次都返回同一条
    "run events": { ok: true, exit: 0, data: { job: "j1", state: "running", events: [{ seq: 0, t: 1, type: "step", text: "先出エウレカ！", page: "SELECT_PITEM", action: "PlayCard", source: "bench" }] } },
    "run status": { ok: true, exit: 0, data: { current: job, recent: [] } },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <Cockpit jobId="j1" onBack={() => {}} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getAllByText("先出エウレカ！").length).toBe(1));
  await waitFor(() => expect(fake.calls.filter((c) => c[1] === "events").length).toBeGreaterThan(1), { timeout: 3000 });
  expect(screen.getAllByText("先出エウレカ！").length).toBe(1);
  expect(screen.getAllByText("选 P 道具").length).toBeGreaterThan(0);
  expect(screen.getByText(/出牌/)).toBeTruthy();
  expect(screen.queryByText(/SELECT_PITEM|PlayCard/)).toBeNull();
});
