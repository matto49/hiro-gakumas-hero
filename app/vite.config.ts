import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwind()],
  server: {
    // mygo dev 加载这个地址（mygo.config.ts 的 devUrl）
    port: 5173,
    strictPort: true,
    watch: { ignored: ["**/.mygo/**", "**/build/**"] },
  },
});
