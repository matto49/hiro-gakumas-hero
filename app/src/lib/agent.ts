// 对话后端：配了模型走内置 agent（Go 侧，见 Task 10）；没配模型只认快捷指令（设计稿 §8）。
import type { Card, Thread } from "./chat";
import type { Caller } from "./hiro";
import { QUICK, runQuick } from "./quick";

export interface Reply {
  text?: string;
  cards: Card[];
}

export interface Agent {
  /** 是否只能用快捷指令（没配模型）。 */
  quickOnly: boolean;
  /** 显示在侧栏的模型名。 */
  label: string;
  ask: (text: string, thread: Thread) => Promise<Reply>;
}

export function quickAgent(call: Caller): Agent {
  return {
    quickOnly: true,
    label: "未配置（快捷指令）",
    ask: async (text) => {
      const q = QUICK.find((x) => x.label === text);
      if (!q) return { text: "DeepSeek 还没填 Key，初星助手只能做下面这几件事。到设置里填好 Key，就可以直接打字了。", cards: [] };
      return { cards: await runQuick(call, q) };
    },
  };
}
