import { useCallback, useEffect, useMemo, useState } from "react";
import { Chat } from "./components/Chat";
import { RightPanel } from "./components/RightPanel";
import { Sidebar, type View } from "./components/Sidebar";
import { quickAgent, type Agent } from "./lib/agent";
import { cardsFor } from "./lib/quick";
import { data, useHiro, type Envelope, type Job, type Summary } from "./lib/hiro";
import { STATE_LABEL } from "./lib/labels";
import { useThreads } from "./lib/threads";
import { Cockpit } from "./pages/Cockpit";
import { InfoPage } from "./pages/InfoPage";

const STATUS_POLL_MS = 2000;

function useStatus() {
  const call = useHiro();
  const [current, setCurrent] = useState<Job | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const pull = async () => {
      try {
        const st = await data<{ current: Job | null }>(call, ["run", "status"]);
        if (live) setCurrent(st.current);
      } catch {
        /* 服务起不来时，对局卡 / 出错卡会说明 */
      }
      if (live) timer = setTimeout(pull, STATUS_POLL_MS);
    };
    pull();
    data<Summary>(call, ["info", "summary"]).then((s) => live && setSummary(s), () => {});
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [call]);
  return { current, summary };
}

export function App({ agent: agentProp }: { agent?: Agent }) {
  const call = useHiro();
  const agent = useMemo(() => agentProp ?? quickAgent(call), [agentProp, call]);
  const t = useThreads();
  const [view, setView] = useState<View>({ name: "chat" });
  const [thinking, setThinking] = useState(false);
  const { current, summary } = useStatus();
  const thread = t.threads.find((x) => x.id === t.active) ?? t.threads[0]!;

  const ask = useCallback(
    async (text: string) => {
      setView({ name: "chat" });
      const tid = thread.id;
      t.add(tid, { role: "user", text, cards: [] });
      setThinking(true);
      try {
        const r = await agent.ask(text, thread);
        t.add(tid, { role: "agent", text: r.text, cards: r.cards });
      } catch (e) {
        t.add(tid, { role: "agent", cards: [{ type: "error", error: { code: "agent", message: `小广出错了：${String(e)}`, hint: "再试一次；一直这样到设置里检查模型" } }] });
      } finally {
        setThinking(false);
      }
    },
    [agent, thread, t],
  );

  const onConfirmResolved = useCallback(
    (msgId: string, i: number, r: Envelope, action: "confirm" | "cancel") => {
      t.updateCards(thread.id, msgId, (cards) => {
        const c = cards[i];
        if (!c || c.type !== "confirm") return cards;
        const status = action === "cancel" ? "cancelled" : r.ok ? "confirmed" : "failed";
        const next = [...cards];
        next[i] = { ...c, status };
        return action === "confirm" ? [...next, ...cardsFor(r)] : next;
      });
    },
    [t, thread.id],
  );

  const liveStatus = current ? `${current.summary} · ${STATE_LABEL[current.state] ?? current.state}` : null;

  return (
    <div className="flex h-full">
      <Sidebar
        threads={t.threads}
        active={thread.id}
        liveJob={current?.id ?? null}
        summary={summary}
        model={agent.label}
        device=""
        onNew={() => {
          t.create();
          setView({ name: "chat" });
        }}
        onPick={(id) => {
          t.setActive(id);
          setView({ name: "chat" });
        }}
        onSettings={() => setView({ name: "settings" })}
      />
      <main className="flex min-w-0 flex-1">
        {view.name === "chat" && (
          <Chat
            thread={thread}
            thinking={thinking}
            status={liveStatus}
            quickOnly={agent.quickOnly}
            h={{
              onSend: ask,
              onQuick: ask,
              onConfirmResolved,
              onOpenJob: (jobId) => setView({ name: "cockpit", jobId }),
              onOpenInfo: (kind) => setView({ name: "info", kind }),
            }}
          />
        )}
        {view.name === "cockpit" && <Cockpit jobId={view.jobId} onBack={() => setView({ name: "chat" })} />}
        {view.name === "info" && (
          <div className="min-w-0 flex-1">
            <InfoPage kind={view.kind} onAsk={ask} />
          </div>
        )}
        {view.name === "settings" && <div className="p-6 text-ink-2">设置（模型、账号密码、环境）在后面的步骤里做。</div>}
      </main>
      {view.name !== "cockpit" && <RightPanel liveJob={current?.id ?? null} summary={summary} onOpenInfo={(kind) => setView({ name: "info", kind })} />}
    </div>
  );
}
