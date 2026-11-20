// 定时轮询，窗口不可见（最小化、被挡在别的桌面）时暂停，回到可见马上补一次。
// fn 返回 false 表示不用再查了（例如任务已经结束）。返回停止函数。
export function pollWhileVisible(fn: () => Promise<boolean | void>, everyMs: number): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let running = false;
  const visible = () => typeof document === "undefined" || !document.hidden;
  const tick = async () => {
    timer = undefined;
    if (stopped || running || !visible()) return;
    running = true;
    let again: boolean | void = true;
    try {
      again = await fn();
    } catch {
      again = true;
    } finally {
      running = false;
    }
    if (again === false) {
      stop();
      return;
    }
    if (!stopped && visible()) timer = setTimeout(tick, everyMs);
  };
  const onVisibility = () => {
    if (visible() && !timer && !running && !stopped) tick();
  };
  const stop = () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisibility);
  };
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisibility);
  tick();
  return stop;
}
