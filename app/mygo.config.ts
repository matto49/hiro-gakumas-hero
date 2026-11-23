import { defineConfig } from "mygo-cli";

export default defineConfig({
  name: "初星助手",
  identifier: "top.matto.hiro-console",
  version: "0.1.0",
  // `mygo dev` runs devCommand and loads devUrl; `mygo build` runs
  // buildCommand and embeds frontendDist into the app.
  devUrl: "http://localhost:5173",
  devCommand: "bun run dev:web",
  buildCommand: "bun run build:web",
  frontendDist: "dist",
  bindings: "src/mygo.ts",
  // 界面文案的消息目录（locales/<语言>.json，生成 locales/catalog_gen.go 和 src/locales.ts）：现在只有简体中文，
  // 它也是源语言，所以 mygo 自己的菜单、对话框按钮和 macOS 的系统面板都显示中文；以后加日文 / 英文放 ja.json / en.json
  i18n: { dir: "locales", source: "zh-Hans" },
  out: "build",
});
