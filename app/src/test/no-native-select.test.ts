// 下拉框一律用 UI 库（components/Field 的 Choice / components/ui/select），不写原生 <select>：
// 原生的在 macOS 上是系统灰色菜单，和界面不搭（用户 11-23）。
import { expect, test } from "bun:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? files(p) : /\.tsx$/.test(n) ? [p] : [];
  });
}

test("界面代码里没有原生 <select> / <option>", () => {
  const root = join(import.meta.dir, "..");
  const bad = files(root).flatMap((f) =>
    readFileSync(f, "utf8")
      .split("\n")
      .map((line, i) => ({ f, i, line: line.replace(/\/\/.*$|\/\*.*?\*\//g, "") }))
      .filter(({ line }) => /<(select|option)[\s>]/.test(line))
      .map(({ f, i }) => `${f.slice(root.length + 1)}:${i + 1}`),
  );
  expect(bad).toEqual([]);
});
