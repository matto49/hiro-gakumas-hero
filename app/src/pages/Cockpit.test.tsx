import { afterEach, expect, test } from "bun:test";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
