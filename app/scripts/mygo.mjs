// 跑 mygo 命令行。不直接用 node_modules/.bin/mygo：在 bun run 下它用 process.execve 换成 mygo 本体，
// 环境变量会丢掉（PATH 没了，mygo 找不到 bun 去读 mygo.config.ts）。这里用 spawn，环境照传。
import { spawn } from "node:child_process";
import { binaryPath } from "mygo-cli";

// makensis 按 locale 读安装脚本：没设 LANG 时（某些终端 / CI）读不了中文名，报 Bad text encoding
const env = { ...process.env };
if (!/UTF-?8/i.test(env.LC_ALL || env.LANG || "")) env.LC_ALL = env.LANG = "en_US.UTF-8";
const child = spawn(binaryPath(), process.argv.slice(2), { stdio: "inherit", env });
child.on("exit", (code, signal) => process.exit(signal ? 1 : (code ?? 1)));
