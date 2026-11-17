import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AssistantCtx, type AssistantApi, type Settings } from "../lib/assistant";
import { HiroCtx } from "../lib/hiro";
import { fakeCaller } from "../test/fake";
import { BootCtx, type BootApi } from "../lib/bootstrap";
import { Wizard } from "./Wizard";

const readyBoot: BootApi = { status: async () => ({ ready: true, steps: [] }), run: async () => {} };

afterEach(cleanup);

const doctor = {
  ok: true,
  exit: 0,
  data: {
    ready: false,
    items: [
      { id: "python", title: "Python 环境", ok: true, detail: "Python 3.10.14，依赖齐全", next: "", optional: false },
      { id: "platform", title: "游戏平台", ok: null, detail: "Mac · PlayCover：还没试连游戏", next: "打开游戏停在首页，再点「试连」", optional: false },
      { id: "sound", title: "游戏声音", ok: null, detail: "程序看不到游戏里的声音设置", next: "关掉", optional: false },
      { id: "model", title: "AI 模型", ok: false, detail: "还没配", next: "", optional: true },
      { id: "secrets", title: "游戏账号密码", ok: false, detail: "没保存", next: "", optional: true },
      { id: "catalog", title: "游戏资料库", ok: false, detail: "还没下载", next: "运行 hiro catalog sync", optional: false },
      { id: "scan", title: "账号信息", ok: false, detail: "还没扫描过账号", next: "", optional: false },
    ],
  },
};

test("向导：六步走完后记下 wizard_done", async () => {
  const hiro = fakeCaller({
    doctor,
    "catalog sync": { ok: true, exit: 0, data: { counts: { cards: 476 } } },
    "secret list": { ok: true, exit: 0, data: { secrets: [] } },
  });
  let s: Settings = { provider: "deepseek", has_key: false };
  const saved: Settings[] = [];
  const api: AssistantApi = {
    settings: async () => s,
    save: async (n) => {
      saved.push(n);
      s = n;
    },
    test: async () => ({ ok: true, message: "" }),
    ask: async () => ({ cards: [] }),
    listThreads: async () => [],
    saveThread: async () => {},
    deleteThread: async () => {},
  };
  let finished = false;
  render(
    <BootCtx.Provider value={readyBoot}>
      <HiroCtx.Provider value={hiro.call}>
        <AssistantCtx.Provider value={api}>
          <Wizard onDone={() => (finished = true)} />
        </AssistantCtx.Provider>
      </HiroCtx.Provider>
    </BootCtx.Provider>,
  );
  // 1 装环境：显示自检结果，下载资料库
  await waitFor(() => expect(screen.getByText("Python 3.10.14，依赖齐全")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "下载资料库" }));
  await waitFor(() => expect(hiro.calls.some((c) => c.join(" ") === "catalog sync")).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  // 2 连游戏
  await waitFor(() => expect(screen.getByRole("button", { name: "试连" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  // 3 声音：勾了才能下一步
  const next = screen.getByRole("button", { name: "下一步" }) as HTMLButtonElement;
  expect(next.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText("我在游戏里关好了"));
  expect((screen.getByRole("button", { name: "下一步" }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  // 4 模型：可以跳过
  await waitFor(() => expect(screen.getByRole("button", { name: "跳过" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "跳过" }));
  // 5 账号密码：跳过
  await waitFor(() => expect(screen.getByText("游戏账号")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "跳过" }));
  // 6 首次扫描 → 完成
  await waitFor(() => expect(screen.getByRole("button", { name: "完成" })).toBeTruthy());
  expect(screen.getByRole("button", { name: "读首页数值" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "检查游戏设置" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "完成" }));
  await waitFor(() => expect(finished).toBe(true));
  expect(saved[saved.length - 1]!.wizard_done).toBe(true);
});


test("没装环境时先装：点「安装」，输出一行行显示，装完重新自检", async () => {
  let ready = false;
  const lines: string[] = [];
  const boot: BootApi = {
    status: async () => (ready ? { ready: true, steps: [] } : { ready: false, steps: [{ id: "setup", title: "安装 Python、依赖和游戏数据（约 5 分钟）" }] }),
    run: async (onLine) => {
      onLine({ step: "setup", line: "==> 2/7 虚拟环境" });
      lines.push("ran");
      ready = true;
    },
  };
  const hiro = fakeCaller({ doctor });
  const api: AssistantApi = {
    settings: async () => ({ provider: "deepseek", has_key: false }),
    save: async () => {},
    test: async () => ({ ok: true, message: "" }),
    ask: async () => ({ cards: [] }),
    listThreads: async () => [],
    saveThread: async () => {},
    deleteThread: async () => {},
  };
  render(
    <BootCtx.Provider value={boot}>
      <HiroCtx.Provider value={hiro.call}>
        <AssistantCtx.Provider value={api}>
          <Wizard onDone={() => {}} />
        </AssistantCtx.Provider>
      </HiroCtx.Provider>
    </BootCtx.Provider>,
  );
  await waitFor(() => expect(screen.getByText("安装 Python、依赖和游戏数据（约 5 分钟）")).toBeTruthy());
  expect(hiro.calls.length).toBe(0); // 没装好之前不调工具层
  fireEvent.click(screen.getByRole("button", { name: "安装" }));
  await waitFor(() => expect(screen.getByText("==> 2/7 虚拟环境")).toBeTruthy());
  await waitFor(() => expect(hiro.calls.some((c) => c[0] === "doctor")).toBe(true));
  expect(lines).toEqual(["ran"]);
});
