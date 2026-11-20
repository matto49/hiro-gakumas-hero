import { afterEach, expect, test } from "bun:test";
import { cleanup, render } from "@testing-library/react";
import { RichText } from "./Rich";

afterEach(cleanup);

test("加粗、列表、行内代码按格式显示，不露出星号", () => {
  const { container } = render(<RichText text={"一共 **45 张**：\n\n- **Vo**：19 张\n- Da：8 张\n\n用 `hiro info` 查。"} />);
  expect(container.textContent).not.toContain("**");
  expect(container.textContent).not.toContain("`");
  const strong = [...container.querySelectorAll("strong")].map((e) => e.textContent);
  expect(strong).toEqual(["45 张", "Vo"]);
  expect(container.querySelectorAll("li").length).toBe(2);
  expect(container.querySelector("code")?.textContent).toBe("hiro info");
});

test("不认识的写法原样显示，不当成 HTML", () => {
  const { container } = render(<RichText text={"<b>x</b> 和 *单星*"} />);
  expect(container.querySelector("b")).toBeNull();
  expect(container.textContent).toBe("<b>x</b> 和 *单星*");
});

test("对话里初星助手的回答用格式显示", async () => {
  const { Chat } = await import("./Chat");
  const thread = { id: "t", title: "x", updatedAt: 0, messages: [{ id: "1", role: "agent" as const, text: "一共 **45 张**", cards: [], at: 0 }] };
  const h = { onSend: () => {}, onQuick: () => {}, onConfirmResolved: () => {}, onOpenJob: () => {}, onOpenInfo: () => {} };
  const { container } = render(<Chat thread={thread} thinking={false} status={null} quickOnly={false} h={h} />);
  expect(container.querySelector("strong")?.textContent).toBe("45 张");
});
