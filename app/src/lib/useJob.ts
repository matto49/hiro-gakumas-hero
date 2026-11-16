// 盯一个任务：每秒拉一次新事件，直到任务结束。对局卡、驾驶舱、右栏共用。
import { useCallback, useEffect, useRef, useState } from "react";
import { LIVE_STATES, data, useHiro, type Job, type JobEvent } from "./hiro";

export interface JobView {
  job: Job | null;
  state: string | null;
  events: JobEvent[];
  steps: JobEvent[];
  lastFrame: string | null;
  refresh: () => void;
}

const POLL_MS = 1000;

export function useJob(jobId: string | null): JobView {
  const call = useHiro();
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [state, setState] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const next = useRef(0);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    next.current = 0;
    setEvents([]);
    setState(null);
    setJob(null);
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const pull = async () => {
      try {
        const r = await data<{ state: string; events: JobEvent[] }>(call, ["run", "events", "--job", jobId, "--since", String(next.current)]);
        if (!live) return;
        if (r.events.length) {
          next.current = r.events[r.events.length - 1]!.seq + 1;
          setEvents((old) => [...old, ...r.events]);
        }
        setState(r.state);
        const st = await data<{ current: Job | null; recent: Job[] }>(call, ["run", "status"]);
        if (!live) return;
        setJob((st.current?.id === jobId ? st.current : st.recent.find((j) => j.id === jobId)) ?? null);
        if (LIVE_STATES.has(r.state)) timer = setTimeout(pull, POLL_MS);
      } catch {
        if (live) timer = setTimeout(pull, POLL_MS * 3);
      }
    };
    pull();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, call, tick]);

  const steps = events.filter((e) => e.type === "step");
  let lastFrame: string | null = null;
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i]!.frame) {
      lastFrame = steps[i]!.frame!;
      break;
    }
  }
  const refresh = useCallback(() => setTick((t) => t + 1), []);
  return { job, state, events, steps, lastFrame, refresh };
}
