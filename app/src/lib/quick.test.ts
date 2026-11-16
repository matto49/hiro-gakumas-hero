import { expect, test } from "bun:test";
import { fakeCaller } from "../test/fake";
import { runQuick, QUICK } from "./quick";
import { sourceLabel } from "./labels";

test("快捷指令：会动游戏的先出确认卡", async () => {
  const fake = fakeCaller({
    "run start daily": { ok: true, exit: 3, data: {}, pending: { id: "p1", command: "run start", summary: "领日常奖励", expires_at: 1 } },
  });
  const cards = await runQuick(fake.call, QUICK.find((q) => q.label === "帮我领日常奖励")!);
  expect(fake.calls).toEqual([["run", "start", "daily"]]);
  expect(cards).toEqual([{ type: "confirm", pending: { id: "p1", command: "run start", summary: "领日常奖励", expires_at: 1 }, status: "waiting" }]);
});

test("快捷指令：查询直接出信息卡", async () => {
  const fake = fakeCaller({
    "info supports": { ok: true, exit: 0, data: { kind: "supports", scanned_at: "2026-11-16 18:00:00", items: [{ name: "a", rarity: "SSR" }] } },
  });
  const cards = await runQuick(fake.call, QUICK.find((q) => q.label === "我的 SSR 支援卡")!);
  expect(cards[0]!.type).toBe("info");
});

test("快捷指令：失败出出错卡", async () => {
  const fake = fakeCaller({ "run start daily": { ok: false, exit: 1, error: { code: "busy", message: "忙", hint: "等" } } });
  const cards = await runQuick(fake.call, QUICK.find((q) => q.label === "帮我领日常奖励")!);
  expect(cards).toEqual([{ type: "error", error: { code: "busy", message: "忙", hint: "等" } }]);
});

test("决策出处的叫法", () => {
  expect(sourceLabel("rule")).toBe("规则");
  expect(sourceLabel("bench")).toBe("算法");
  expect(sourceLabel("search")).toBe("算法");
  expect(sourceLabel("llm")).toBe("LLM");
  expect(sourceLabel("rescue")).toBe("兜底");
  expect(sourceLabel(null)).toBe("自动");
});
