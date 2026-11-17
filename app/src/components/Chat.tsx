// 中间的对话（设计稿 §5，画板 q2-a）：消息里嵌卡片，底部输入框 + 建议问题。
import { useEffect, useRef, useState } from "react";
import type { Card, Message, Thread } from "../lib/chat";
import type { Envelope } from "../lib/hiro";
import { QUICK } from "../lib/quick";
import { ConfirmCard, ErrorCard, FormationCard, InfoCard, RunCard } from "./Cards";
import { RichText } from "./Rich";
import { Dot } from "./ui";

export interface ChatHandlers {
  onSend: (text: string) => void;
  onQuick: (label: string) => void;
  onConfirmResolved: (msgId: string, cardIndex: number, r: Envelope, action: "confirm" | "cancel") => void;
  onOpenJob: (jobId: string) => void;
  onOpenInfo: (kind: "supports" | "memories" | "idols" | "affection" | "catalog") => void;
}

function CardView({ card, msg, i, h }: { card: Card; msg: Message; i: number; h: ChatHandlers }) {
  switch (card.type) {
    case "confirm":
      return <ConfirmCard card={card} onResolved={(r, a) => h.onConfirmResolved(msg.id, i, r, a)} />;
    case "run":
      return <RunCard card={card} onOpen={h.onOpenJob} />;
    case "info":
      return <InfoCard card={card} onOpen={(k) => k !== "summary" && h.onOpenInfo(k)} />;
    case "error":
      return <ErrorCard card={card} />;
    case "formation":
      return <FormationCard card={card} />;
  }
}

export function Chat({ thread, thinking, status, quickOnly, h }: { thread: Thread; thinking: boolean; status: string | null; quickOnly: boolean; h: ChatHandlers }) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [thread.messages.length, thinking]);
  const send = () => {
    const t = text.trim();
    if (!t) return;
    setText("");
    h.onSend(t);
  };
  return (
    <section className="flex min-w-0 flex-1 flex-col">
      <header className="flex h-12 flex-none items-center gap-2.5 border-b border-line bg-panel px-5">
        <b className="text-sm">{thread.title}</b>
        {status && (
          <span className="inline-flex h-[22px] items-center gap-1.5 rounded-full bg-chip px-2.5 text-[11.5px] text-ink-2">
            <Dot tone="acc" />
            {status}
          </span>
        )}
      </header>
      <div className="flex min-h-0 flex-1 flex-col gap-3.5 overflow-auto px-7 py-[18px]">
        {thread.messages.length === 0 && (
          <div className="m-auto max-w-[420px] text-center text-ink-2">
            <div className="text-[17px] font-bold text-ink">今天想让小广做什么？</div>
            <div className="mt-1.5">{quickOnly ? "DeepSeek 还没填 Key：先点下面的快捷指令。到设置里填好 Key 就能直接打字。" : "直接打字，或者点下面的建议。"}</div>
          </div>
        )}
        {thread.messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="max-w-[70%] self-end rounded-[14px_14px_4px_14px] bg-ink px-3.5 py-2 whitespace-pre-wrap text-white">
              {m.text}
            </div>
          ) : (
            <div key={m.id} className="flex max-w-[92%] gap-2.5">
              <div className="size-7 flex-none rounded-full bg-acc-t" />
              <div className="flex min-w-0 flex-col gap-2">
                {m.text && <RichText text={m.text} />}
                {m.cards.map((c, i) => (
                  <CardView key={i} card={c} msg={m} i={i} h={h} />
                ))}
              </div>
            </div>
          ),
        )}
        {thinking && <div className="text-ink-3">小广在想…</div>}
        <div ref={end} />
      </div>
      <div className="mx-7 mb-[18px] rounded-[14px] border border-line bg-panel px-3 py-2.5">
        <textarea
          className="h-[22px] max-h-32 w-full resize-none bg-transparent outline-none placeholder:text-ink-3 disabled:cursor-not-allowed"
          placeholder={quickOnly ? "DeepSeek 还没填 Key，先用下面的快捷指令（左下角「模型」进设置填）" : "让小广做点什么，例如：帮我领日常奖励"}
          value={text}
          disabled={quickOnly}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
        />
        <div className="mt-2 flex flex-wrap gap-1.5">
          {QUICK.map((q) => (
            <button type="button" key={q.label} className="rounded-xl border border-line px-2.5 py-px text-[11.5px] text-ink-2 hover:border-acc hover:text-acc" onClick={() => h.onQuick(q.label)}>
              {q.label}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
