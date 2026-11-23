import { describe, expect, test } from "bun:test";
import { deviceFromDoctor } from "./device";

describe("deviceFromDoctor（侧栏的游戏连接状态）", () => {
  test("连上：显示平台名", () => {
    const d = deviceFromDoctor([{ id: "platform", ok: true, detail: "Mac · PlayCover：已连上，画面 720×1280", next: "" }]);
    expect(d).toEqual({ tone: "ok", text: "Mac · PlayCover 已连上", title: "Mac · PlayCover：已连上，画面 720×1280" });
  });
  test("连不上：说原因和下一步，可以点了重试", () => {
    const d = deviceFromDoctor([
      { id: "platform", ok: false, detail: "Mac · PlayCover：连不上游戏（refused）", next: "确认游戏开着" },
    ]);
    expect(d.tone).toBe("err");
    expect(d.text).toBe("游戏未连接 · 点这里重试");
    expect(d.title).toContain("确认游戏开着");
  });
  test("没有平台项：当成没连上", () => {
    expect(deviceFromDoctor([]).tone).toBe("err");
  });
});
