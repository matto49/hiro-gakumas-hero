// 第一次打开时装 Python 环境（Go 侧 Bootstrap）。组件经 BootCtx 拿，测试里换成假的。
import { createContext, useContext } from "react";

export interface BootStep {
  id: string;
  title: string;
}

export interface BootStatus {
  ready: boolean;
  steps: BootStep[];
  error?: { code: string; message: string; hint?: string };
}

export interface BootApi {
  status: () => Promise<BootStatus>;
  /** 去掉「装完」标记，下次 run 重跑安装脚本。 */
  reset?: () => Promise<void>;
  /** 一步步装，每行输出交给 onLine；失败时抛错（message 是白话）。 */
  run: (onLine: (p: { step: string; line: string }) => void) => Promise<void>;
}

export const realBoot: BootApi = {
  status: async () => (await import("../mygo")).Bootstrap.status() as Promise<BootStatus>,
  reset: async () => (await import("../mygo")).Bootstrap.reset(),
  run: async (onLine) => {
    const { Bootstrap } = await import("../mygo");
    const { Channel } = await import("mygo-runtime");
    await Bootstrap.run(new Channel(onLine));
  },
};

export const BootCtx = createContext<BootApi>(realBoot);

export function useBoot(): BootApi {
  return useContext(BootCtx);
}
