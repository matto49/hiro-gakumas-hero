import path from "node:path";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";
import { defineConfig } from "vite";

// 界面预览：只打包 preview/index.html（假数据），输出到 .preview/，不进程序
export default defineConfig({
  plugins: [react(), tailwind()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  base: "./",
  build: { outDir: ".preview", emptyOutDir: true, rollupOptions: { input: path.resolve(import.meta.dirname, "preview/index.html") }, modulePreload: false },
});
