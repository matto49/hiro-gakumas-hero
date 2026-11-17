import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chat } from "./components/Chat";
import { RightPanel } from "./components/RightPanel";
import { Sidebar, type View } from "./components/Sidebar";
import { quickAgent, type Agent } from "./lib/agent";
import { modelAgent, modelLabel, useAssistant, type Settings } from "./lib/assistant";
import type { Thread } from "./lib/chat";
import { cardsFor } from "./lib/quick";
import { data, useHiro, type Envelope, type Job, type Summary } from "./lib/hiro";
import { STATE_LABEL } from "./lib/labels";
import { useThreads } from "./lib/threads";
import { Cockpit } from "./pages/Cockpit";
import { InfoPage } from "./pages/InfoPage";
import { SettingsPage } from "./pages/Settings";
import { Wizard } from "./pages/Wizard";

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

/** 对话存到本机（Go 侧 Threads）：启动时读回来，改了半秒后写回去。 */
function usePersistence(t: ReturnType<typeof useThreads>) {
  const api = useAssistant();
  const loaded = useRef(false);
  const saved = useRef(new Map<string, number>());
  useEffect(() => {
    api
      .listThreads()
      .then((list) => {
        const ok = list.filter((x): x is Thread => !!x && Array.isArray((x as Thread).messages));
        if (ok.length) {
          ok.forEach((x) => saved.current.set(x.id, x.updatedAt));
          t.setThreads(ok);
          t.setActive(ok[0]!.id);
        }
      })
      .catch(() => {})
      .finally(() => (loaded.current = true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api]);
  useEffect(() => {
    if (!loaded.current) return;
    const timer = setTimeout(() => {
      for (const th of t.threads) {
        if (th.messages.length && saved.current.get(th.id) !== th.updatedAt) {
          saved.current.set(th.id, th.updatedAt);
          api.saveThread(th).catch(() => {});
        }
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [t.threads, api]);
}

export function App({ agent: agentProp }: { agent?: Agent }) {
  const call = useHiro();
  const api = useAssistant();
  const [settings, setSettings] = useState<Settings | null>(null);
  const reloadSettings = useCallback(() => {
    api.settings().then(setSettings, () => setSettings({ provider: "deepseek", has_key: false }));
  }, [api]);
  useEffect(reloadSettings, [reloadSettings]);
  const quick = useMemo(() => quickAgent(call), [call]);
  const agent = useMemo(() => {
    if (agentProp) return agentProp;
    const ready = settings && (settings.provider === "codex" || settings.provider === "claude" || (settings.provider === "deepseek" && settings.has_key));
    return ready ? modelAgent(api, settings!) : { ...quick, label: modelLabel(settings) };
  }, [agentProp, settings, api, quick]);
  const t = useThreads();
  usePersistence(t);
  const [view, setView] = useState<View>({ name: "chat" });
  const [thinking, setThinking] = useState(false);
  const { current, summary } = useStatus();
  const thread = t.threads.find((x) => x.id === t.active) ?? t.threads[0]!;

  const askWith = useCallback(
    async (who: Agent, text: string) => {
      setView({ name: "chat" });
      const tid = thread.id;
      t.add(tid, { role: "user", text, cards: [] });
      setThinking(true);
      try {
        const r = await who.ask(text, thread);
        t.add(tid, { role: "agent", text: r.text, cards: r.cards });
      } catch (e) {
        t.add(tid, { role: "agent", cards: [{ type: "error", error: { code: "agent", message: `小广出错了：${String(e)}`, hint: "再试一次；一直这样到设置里检查模型" } }] });
      } finally {
        setThinking(false);
      }
    },
    [thread, t],
  );
  const ask = useCallback((text: string) => askWith(agent, text), [askWith, agent]);
  const onQuick = useCallback((text: string) => askWith(quick, text), [askWith, quick]);

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

  if (settings && !settings.wizard_done && !agentProp) return <Wizard onDone={reloadSettings} />;

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
              onQuick,
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
        {view.name === "settings" && (
          <div className="min-w-0 flex-1">
            <SettingsPage onChanged={reloadSettings} />
          </div>
        )}
      </main>
      {view.name !== "cockpit" && <RightPanel liveJob={current?.id ?? null} summary={summary} onOpenInfo={(kind) => setView({ name: "info", kind })} />}
    </div>
  );
}
