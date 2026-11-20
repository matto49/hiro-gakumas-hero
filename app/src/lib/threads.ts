// 对话列表的状态。
import { useCallback, useState } from "react";
import { newThread, uid, type Card, type Message, type Thread } from "./chat";

export function useThreads(initial?: Thread[]) {
  const [threads, setThreads] = useState<Thread[]>(() => (initial?.length ? initial : [newThread()]));
  const [active, setActive] = useState<string>(() => threads[0]!.id);

  const patch = useCallback((id: string, f: (t: Thread) => Thread) => {
    setThreads((ts) => ts.map((t) => (t.id === id ? { ...f(t), updatedAt: Date.now() } : t)));
  }, []);

  const add = useCallback(
    (threadId: string, m: Omit<Message, "id" | "at">): string => {
      const id = uid();
      patch(threadId, (t) => ({
        ...t,
        title: t.messages.length === 0 && m.role === "user" && m.text ? m.text.slice(0, 24) : t.title,
        messages: [...t.messages, { ...m, id, at: Date.now() }],
      }));
      return id;
    },
    [patch],
  );

  const updateCards = useCallback(
    (threadId: string, msgId: string, f: (cards: Card[]) => Card[]) => {
      patch(threadId, (t) => ({ ...t, messages: t.messages.map((m) => (m.id === msgId ? { ...m, cards: f(m.cards) } : m)) }));
    },
    [patch],
  );

  const create = useCallback(() => {
    const t = newThread();
    setThreads((ts) => [t, ...ts]);
    setActive(t.id);
    return t.id;
  }, []);

  return { threads, active, setActive, add, updateCards, create, setThreads };
}
