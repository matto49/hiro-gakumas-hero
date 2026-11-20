// 中间的对话（设计稿 §5，画板 q2-a）：消息里嵌卡片，底部输入框 + 建议问题。
import { useEffect, useRef, useState } from "react";
import type { Card, Message, PageKind, Thread } from "../lib/chat";
import type { Envelope } from "../lib/hiro";
import { QUICK } from "../lib/quick";
import { ConfirmCard, ErrorCard, FormationCard, InfoCard, RunCard } from "./Cards";
import { RichText } from "./Rich";
import { Dot, Logo } from "./ui";

export interface ChatHandlers {
  onSend: (text: string) => void;
  onQuick: (label: string) => void;
  onConfirmResolved: (msgId: string, cardIndex: number, r: Envelope, action: "confirm" | "cancel") => void;
  onOpenJob: (jobId: string) => void;
  onOpenInfo: (kind: PageKind) => void;
  onOpenPlan?: () => void;
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
      return <FormationCard card={card} onOpen={h.onOpenPlan} />;
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
    <section className="flex min-w-0 flex-1 flex-col bg-surface">
      <header className="flex h-12 flex-none items-center gap-3 border-b border-line px-6">
        <b className="min-w-0 truncate text-[14px]">{thread.messages.length ? thread.title : "新对话"}</b>
        {status && (
          <span className="inline-flex h-6 flex-none items-center gap-1.5 rounded-full border border-brand/40 bg-brand-tint px-2.5 text-[11.5px] text-brand-ink">
            <Dot tone="acc" pulse />
            {status}
          </span>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto flex max-w-[760px] flex-col px-8 py-6">
          {thread.messages.length === 0 && (
            <div className="mt-[12vh] flex flex-col gap-2">
              <Logo size={26} />
              <div className="mt-2 text-[20px] font-bold tracking-tight text-ink">今天想让初星助手做什么？</div>
              <div className="max-w-[460px] text-ink-2">
                {quickOnly ? "DeepSeek 还没填 Key：先点下面的快捷指令。到设置里填好 Key 就能直接打字。" : "直接打字，或者点下面的建议。要开一局，左边「开局」更直接。"}
              </div>
            </div>
          )}
          {thread.messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="mt-6 flex items-baseline gap-3 first:mt-0">
                <span className="w-9 flex-none text-right text-[11px] font-medium text-ink-3">你</span>
                <div className="min-w-0 flex-1 text-[14px] font-semibold whitespace-pre-wrap text-ink">{m.text}</div>
              </div>
            ) : (
              <div key={m.id} className="mt-2.5 flex gap-3">
                <span className="flex w-9 flex-none justify-end pt-[3px]">
                  <Logo size={13} />
                </span>
                <div className="flex min-w-0 flex-1 flex-col gap-2.5 border-l border-line pl-4 text-ink">
                  {m.text && <RichText text={m.text} />}
                  {m.cards.map((c, i) => (
                    <CardView key={i} card={c} msg={m} i={i} h={h} />
                  ))}
                </div>
              </div>
            ),
          )}
          {thinking && (
            <div className="mt-2.5 flex gap-3">
              <span className="w-9 flex-none" />
              <div className="flex items-center gap-2 border-l border-line pl-4 text-ink-3">
                <Dot tone="acc" pulse />
                初星助手在想…
              </div>
            </div>
          )}
          <div ref={end} />
        </div>
      </div>
      <div className="flex-none border-t border-line bg-surface-2 px-6 pt-3 pb-4">
        <div className="mx-auto max-w-[760px]">
          <div className="rounded-xl border border-line-strong bg-surface px-3.5 py-2.5 transition-colors focus-within:border-brand focus-within:ring-3 focus-within:ring-brand/20">
            <textarea
              className="h-[22px] max-h-32 w-full resize-none bg-transparent text-[13.5px] outline-none placeholder:text-ink-3 disabled:cursor-not-allowed"
              placeholder={quickOnly ? "DeepSeek 还没填 Key，先用下面的快捷指令（左下角「模型」进设置填）" : "让初星助手做点什么，例如：帮我领日常奖励"}
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
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {QUICK.map((q) => (
              <button
                type="button"
                key={q.label}
                className="h-6 rounded-full border border-line-strong bg-surface px-2.5 text-[11.5px] text-ink-2 transition-colors hover:border-brand hover:text-brand-ink"
                onClick={() => h.onQuick(q.label)}
              >
                {q.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
