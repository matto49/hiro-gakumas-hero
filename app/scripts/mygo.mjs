// 跑 mygo 命令行。不直接用 node_modules/.bin/mygo：在 bun run 下它用 process.execve 换成 mygo 本体，
// 环境变量会丢掉（PATH 没了，mygo 找不到 bun 去读 mygo.config.ts）。这里用 spawn，环境照传。
import { spawn } from "node:child_process";
import { binaryPath } from "mygo-cli";

const child = spawn(binaryPath(), process.argv.slice(2), { stdio: "inherit", env: process.env });
child.on("exit", (code, signal) => process.exit(signal ? 1 : (code ?? 1)));
