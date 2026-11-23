import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AssistantCtx, type AssistantApi, type Settings } from "../lib/assistant";
import { HiroCtx } from "../lib/hiro";
import { choiceState, choose, fakeCaller } from "../test/fake";
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
  const fake = fakeAssistant({ provider: "deepseek", has_key: false });
  render(
    <HiroCtx.Provider value={hiro.call}>
      <AssistantCtx.Provider value={fake.api}>
        <SettingsPage onChanged={() => {}} />
      </AssistantCtx.Provider>
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByLabelText("服务商")).toBeTruthy());
  const prov = await choiceState("服务商");
  expect(prov.shown).toContain("DeepSeek");
  expect(prov.options).toEqual(["不用模型", "DeepSeek（推荐）", "Kimi（Moonshot）", "通义千问", "智谱 GLM", "OpenAI", "OpenRouter", "自定义（OpenAI 兼容）"]);
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
  const fake = fakeAssistant({ provider: "deepseek", has_key: false });
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

test("填了 Key 后模型是从 DeepSeek 拉回来的下拉框，默认 flash", async () => {
  const fake = fakeAssistant({ provider: "deepseek", has_key: true, model: "deepseek-flash" });
  fake.api.models = async () => ["deepseek-flash", "deepseek-v4-pro"];
  render(
    <HiroCtx.Provider value={hiro.call}>
      <AssistantCtx.Provider value={fake.api}>
        <SettingsPage onChanged={() => {}} />
      </AssistantCtx.Provider>
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect((screen.getByLabelText("模型") as HTMLElement).tagName).toBe("BUTTON"));
  const st = await choiceState("模型");
  expect(st.options).toEqual(["deepseek-flash（默认）", "deepseek-v4-pro"]);
  expect(st.shown).toContain("deepseek-flash");
  await choose("模型", "deepseek-v4-pro");
  await waitFor(() => expect(fake.saved.at(-1)![0].model).toBe("deepseek-v4-pro"));
});

test("没填 Key 时模型是手填框，默认 deepseek-flash", async () => {
  const fake = fakeAssistant({ provider: "deepseek", has_key: false });
  render(
    <HiroCtx.Provider value={hiro.call}>
      <AssistantCtx.Provider value={fake.api}>
        <SettingsPage onChanged={() => {}} />
      </AssistantCtx.Provider>
    </HiroCtx.Provider>,
  );
  const inp = (await screen.findByLabelText("模型")) as HTMLInputElement;
  expect(inp.tagName).toBe("INPUT");
  expect(inp.value).toBe("deepseek-flash");
});

test("换成 Kimi：填 Key 后按 Kimi 列模型，选一个后保存时 Key 存到 Kimi 名下", async () => {
  const fake = fakeAssistant({ provider: "deepseek", has_key: true, has_keys: { deepseek: true } });
  const asked: [string, string, string][] = [];
  fake.api.models = async (p, url, key) => {
    asked.push([p, url, key]);
    return ["moonshot-v1-8k", "kimi-k2"];
  };
  render(
    <HiroCtx.Provider value={hiro.call}>
      <AssistantCtx.Provider value={fake.api}>
        <SettingsPage onChanged={() => {}} />
      </AssistantCtx.Provider>
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByLabelText("服务商")).toBeTruthy());
  await choose("服务商", "Kimi（Moonshot）");
  expect(screen.getByText(/还没填/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "sk-kimi" } });
  await waitFor(() => expect(asked.some(([p, , k]) => p === "kimi" && k === "sk-kimi")).toBe(true));
  await waitFor(() => expect((screen.getByLabelText("模型") as HTMLElement).tagName).toBe("BUTTON"));
  await choose("模型", "kimi-k2");
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(fake.saved.at(-1)?.[0].provider).toBe("kimi"));
  expect(fake.saved.at(-1)![0].model).toBe("kimi-k2");
  expect(fake.saved.at(-1)![1]).toBe("sk-kimi");
});

test("本机命令行收在「高级」里，默认不展开；正在用命令行时展开", async () => {
  const fake = fakeAssistant({ provider: "deepseek", has_key: false });
  const { unmount } = render(
    <HiroCtx.Provider value={hiro.call}>
      <AssistantCtx.Provider value={fake.api}>
        <SettingsPage onChanged={() => {}} />
      </AssistantCtx.Provider>
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText("高级：用本机命令行")).toBeTruthy());
  expect((screen.getByText("高级：用本机命令行").closest("details") as HTMLDetailsElement).open).toBe(false);
  unmount();
  const cli = fakeAssistant({ provider: "claude", has_key: false });
  render(
    <HiroCtx.Provider value={hiro.call}>
      <AssistantCtx.Provider value={cli.api}>
        <SettingsPage onChanged={() => {}} />
      </AssistantCtx.Provider>
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect((screen.getByText("高级：用本机命令行").closest("details") as HTMLDetailsElement).open).toBe(true));
  expect(screen.getByText(/现在用的是 Claude Code/)).toBeTruthy();
});
