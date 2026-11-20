// 编 Go 版 hiro（go/cmd/hiro）放进 resources/<平台>/bin/，随安装包带（go/README.md 阶段 1）。
// 阶段 1 的 Go 代码不用 cgo（SQLite 是纯 Go），Mac 上能直接编出 Windows 版。
//   bun run build-hiro                 全部平台
//   bun run build-hiro darwin-arm64    只编这几个
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { join, resolve } from "node:path";

const TARGETS = {
  "darwin-arm64": { goos: "darwin", goarch: "arm64", bin: "hiro" },
  "darwin-amd64": { goos: "darwin", goarch: "amd64", bin: "hiro" },
  "windows-amd64": { goos: "windows", goarch: "amd64", bin: "hiro.exe" },
};
const only = process.argv.slice(2);
const goDir = resolve("..", "go");

for (const [plat, t] of Object.entries(TARGETS)) {
  if (only.length && !only.includes(plat)) continue;
  const outDir = resolve("resources", plat, "bin");
  mkdirSync(outDir, { recursive: true });
  const out = join(outDir, t.bin);
  console.log(`${plat}: go build → ${out}`);
  execFileSync("go", ["build", "-trimpath", "-ldflags", "-s -w", "-o", out, "./cmd/hiro"], {
    cwd: goDir,
    stdio: "inherit",
    env: { ...process.env, GOOS: t.goos, GOARCH: t.goarch, CGO_ENABLED: "0" },
  });
}
