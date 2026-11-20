// 下载要随安装包带的 uv（装 Python 用）到 resources/<平台>/bin/。构建安装包前跑一次：bun run fetch-uv
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, renameSync, chmodSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const VERSION = process.env.UV_VERSION || "0.13.8";
const TARGETS = {
  "darwin-arm64": { asset: "uv-aarch64-apple-darwin.tar.gz", bin: "uv" },
  "darwin-amd64": { asset: "uv-x86_64-apple-darwin.tar.gz", bin: "uv" },
  "windows-amd64": { asset: "uv-x86_64-pc-windows-msvc.zip", bin: "uv.exe" },
};
const only = process.argv.slice(2);

for (const [plat, t] of Object.entries(TARGETS)) {
  if (only.length && !only.includes(plat)) continue;
  const dest = join("resources", plat, "bin", t.bin);
  if (existsSync(dest)) {
    console.log(`${plat}: 已有 ${dest}`);
    continue;
  }
  const url = `https://github.com/astral-sh/uv/releases/download/${VERSION}/${t.asset}`;
  console.log(`${plat}: 下载 ${url}`);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  const dir = mkdtempSync(join(tmpdir(), "uv-"));
  const file = join(dir, t.asset);
  writeFileSync(file, Buffer.from(await res.arrayBuffer()));
  execFileSync(t.asset.endsWith(".zip") ? "unzip" : "tar", t.asset.endsWith(".zip") ? ["-q", file, "-d", dir] : ["-xzf", file, "-C", dir]);
  const found = execFileSync("find", [dir, "-name", t.bin, "-type", "f"]).toString().trim().split("\n")[0];
  mkdirSync(join("resources", plat, "bin"), { recursive: true });
  renameSync(found, dest);
  chmodSync(dest, 0o755);
}
