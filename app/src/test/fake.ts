import type { Caller, Envelope } from "../lib/hiro";

/** 假的工具层：按命令前缀回信封，记下每次调用。 */
export function fakeCaller(routes: Record<string, Envelope | ((args: string[]) => Envelope)>) {
  const calls: string[][] = [];
  const call: Caller = async (args) => {
    calls.push(args);
    const key = Object.keys(routes)
      .sort((a, b) => b.length - a.length)
      .find((k) => args.join(" ").startsWith(k));
    if (!key) return { ok: false, exit: 2, error: { code: "unknown_command", message: args.join(" ") } };
    const r = routes[key]!;
    return typeof r === "function" ? r(args) : r;
  };
  return { call, calls };
}

/** 在 UI 库的下拉框（components/Field 的 Choice）里选一项：点开触发按钮，再点选项。 */
export async function choose(label: string, option: string | RegExp) {
  const { fireEvent, screen } = await import("@testing-library/react");
  const trigger = screen.getByLabelText(label);
  if (trigger.getAttribute("aria-expanded") !== "true") fireEvent.click(trigger);
  const item = await screen.findByRole("option", { name: option });
  // base-ui 的选项要一整套按下 / 抬起再点击才算选中（只发 click 不认）
  fireEvent.pointerDown(item);
  fireEvent.mouseDown(item);
  fireEvent.pointerUp(item);
  fireEvent.mouseUp(item);
  fireEvent.click(item);
}

/** 下拉框现在显示的文字和全部选项（点开读完再关上）。 */
export async function choiceState(label: string): Promise<{ shown: string; options: string[] }> {
  const { fireEvent, screen } = await import("@testing-library/react");
  const trigger = screen.getByLabelText(label);
  const shown = trigger.textContent ?? "";
  fireEvent.click(trigger);
  const options = (await screen.findAllByRole("option")).map((o) => o.textContent ?? "");
  fireEvent.click(trigger); // 再点一下关上
  return { shown, options };
}
