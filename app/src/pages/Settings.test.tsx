import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AssistantCtx, type AssistantApi, type Settings } from "../lib/assistant";
import { HiroCtx } from "../lib/hiro";
import { fakeCaller } from "../test/fake";
import { SettingsPage } from "./Settings";

afterEach(cleanup);

function fakeAssistant(initial: Settings) {
  let s = initial;
  const saved: [Settings, string][] = [];
  const api: AssistantApi = {
    settings: async () => s,
    save: async (n, key) => {
      saved.push([n, key]);
      s = { ...n, has_key: n.has_key || key !== "" };
    },
    test: async () => ({ ok: true, message: "模型回答：可以" }),
    ask: async () => ({ cards: [] }),
    listThreads: async () => [],
    saveThread: async () => {},
    deleteThread: async () => {},
  };
  return { api, saved };
}

const hiro = fakeCaller({ "secret list": { ok: true, exit: 0, data: { secrets: [{ name: "game-account", saved: true }] } } });

test("填 DeepSeek Key 保存后输入框清空、只显示已保存", async () => {
  const fake = fakeAssistant({ provider: "", has_key: false });
  render(
    <HiroCtx.Provider value={hiro.call}>
      <AssistantCtx.Provider value={fake.api}>
        <SettingsPage onChanged={() => {}} />
      </AssistantCtx.Provider>
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByLabelText("服务商")).toBeTruthy());
  fireEvent.change(screen.getByLabelText("服务商"), { target: { value: "deepseek" } });
  const key = screen.getByLabelText("API Key") as HTMLInputElement;
  expect(key.type).toBe("password");
  fireEvent.change(key, { target: { value: "sk-abc" } });
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(fake.saved.length).toBe(1));
  expect(fake.saved[0]![1]).toBe("sk-abc");
  expect(fake.saved[0]![0].provider).toBe("deepseek");
  await waitFor(() => expect((screen.getByLabelText("API Key") as HTMLInputElement).value).toBe(""));
  expect(screen.getByText(/Key 已保存/)).toBeTruthy();
  expect(document.body.textContent).not.toContain("sk-abc");
});

test("账号密码只显示有没有保存", async () => {
  const fake = fakeAssistant({ provider: "deepseek", has_key: true });
  render(
    <HiroCtx.Provider value={hiro.call}>
      <AssistantCtx.Provider value={fake.api}>
        <SettingsPage onChanged={() => {}} />
      </AssistantCtx.Provider>
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText("游戏账号")).toBeTruthy());
  expect(screen.getAllByText("已保存").length).toBeGreaterThan(0);
  expect(screen.getByText("游戏密码").parentElement!.textContent).toContain("没保存");
});

test("环境：重新安装会先去掉装完标记再跑安装", async () => {
  const { BootCtx } = await import("../lib/bootstrap");
  const order: string[] = [];
  const boot = {
    status: async () => ({ ready: true, steps: [] }),
    reset: async () => void order.push("reset"),
    run: async (onLine: (p: { step: string; line: string }) => void) => {
      order.push("run");
      onLine({ step: "setup", line: "==> 1/7 KAA 子模块" });
    },
  };
  const fake = fakeAssistant({ provider: "", has_key: false });
  render(
    <BootCtx.Provider value={boot}>
      <HiroCtx.Provider value={hiro.call}>
        <AssistantCtx.Provider value={fake.api}>
          <SettingsPage onChanged={() => {}} />
        </AssistantCtx.Provider>
      </HiroCtx.Provider>
    </BootCtx.Provider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "重新安装" }));
  await waitFor(() => expect(screen.getByText("==> 1/7 KAA 子模块")).toBeTruthy());
  expect(order).toEqual(["reset", "run"]);
});
