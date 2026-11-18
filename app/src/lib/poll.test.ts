import { afterEach, expect, test } from "bun:test";
import { pollWhileVisible } from "./poll";

let hidden = false;
Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
afterEach(() => (hidden = false));

test("窗口不可见时不轮询，回到可见马上补一次", async () => {
  let n = 0;
  const stop = pollWhileVisible(async () => void n++, 20);
  await new Promise((r) => setTimeout(r, 70));
  const seen = n;
  expect(seen).toBeGreaterThan(1);
  hidden = true;
  document.dispatchEvent(new Event("visibilitychange"));
  await new Promise((r) => setTimeout(r, 30));
  const atHide = n;
  await new Promise((r) => setTimeout(r, 80));
  expect(n).toBe(atHide);
  hidden = false;
  document.dispatchEvent(new Event("visibilitychange"));
  await new Promise((r) => setTimeout(r, 10));
  expect(n).toBeGreaterThan(atHide);
  stop();
  const atStop = n;
  await new Promise((r) => setTimeout(r, 60));
  expect(n).toBe(atStop);
});

test("回调返回 false 时停止", async () => {
  let n = 0;
  pollWhileVisible(async () => (++n < 3 ? true : false), 10);
  await new Promise((r) => setTimeout(r, 100));
  expect(n).toBe(3);
});
