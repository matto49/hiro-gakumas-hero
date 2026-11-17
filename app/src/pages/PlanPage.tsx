// 编成方案页：出方案（按社区规则，用信息库里的库存）→ 看理由 → 先看差在哪（只看不改）→ 写进游戏。
import { useEffect, useState } from "react";
import { PlanView } from "../components/PlanView";
import { StartTask } from "../components/StartTask";
import { Button, Empty, Panel } from "../components/ui";
import { HiroError, data, useHiro } from "../lib/hiro";
import type { PlanSummary } from "../lib/plan";

const input = "h-9 rounded-lg border border-line bg-white px-3 outline-none focus:border-acc";
const SCENARIOS: [string, string][] = [
  ["hif", "H.I.F"],
  ["hajime", "初"],
  ["nia", "N.I.A"],
];

export function PlanPage({ onOpenJob }: { onOpenJob: (jobId: string) => void }) {
  const call = useHiro();
  const [plan, setPlan] = useState<PlanSummary | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [idol, setIdol] = useState("");
  const [sset, setSset] = useState("");
  const [mset, setMset] = useState("");
  const [attr, setAttr] = useState("");
  const [scenario, setScenario] = useState("hif");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    data<PlanSummary>(call, ["plan", "show"])
      .then((p) => {
        setPlan(p);
        setIdol(p.idol.id ?? "");
        setSset(p.support.set ? String(p.support.set) : "");
        setMset(p.memory.set ? String(p.memory.set) : "");
      })
      .catch((e) => e instanceof HiroError && setErr(e.error));
  }, [call]);

  const build = async () => {
    setBusy(true);
    setErr(null);
    const args = ["plan", "formation", "--idol", idol.trim()];
    if (sset) args.push("--support-set", sset);
    if (mset) args.push("--memory-set", mset);
    if (attr) args.push("--main-attr", attr);
    try {
      setPlan(await data<PlanSummary>(call, args));
    } catch (e) {
      if (e instanceof HiroError) setErr(e.error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-full overflow-auto px-6 py-5">
      <h1 className="m-0 mb-1 text-xl font-bold">开局编成方案</h1>
      <div className="mb-4 text-xs text-ink-2">按社区攻略，用最近一次扫描的支援卡和回忆库存选：支援卡 5 张 + 租借 1 张、回忆 4 份。只计算，不碰游戏。</div>
      <div className="flex max-w-[760px] flex-col gap-4">
        <Panel className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-ink-2">偶像卡</span>
            <input aria-label="偶像卡" className={`${input} w-64`} placeholder="i_card-skin-shro-3-007" value={idol} onChange={(e) => setIdol(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-ink-2">支援卡编成号</span>
            <input aria-label="支援卡编成号" className={`${input} w-24`} inputMode="numeric" placeholder="1–20" value={sset} onChange={(e) => setSset(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-ink-2">回忆编成号</span>
            <input aria-label="回忆编成号" className={`${input} w-24`} inputMode="numeric" placeholder="1–20" value={mset} onChange={(e) => setMset(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-ink-2">主属性</span>
            <select aria-label="主属性" className={input} value={attr} onChange={(e) => setAttr(e.target.value)}>
              <option value="">自动</option>
              <option value="Vo">Vo</option>
              <option value="Da">Da</option>
              <option value="Vi">Vi</option>
            </select>
          </label>
          <Button primary disabled={busy || !idol.trim()} onClick={build}>
            {busy ? "计算中…" : "出方案"}
          </Button>
        </Panel>
        {err && (
          <Empty>
            <div>{err.message}</div>
            {err.hint && <div className="mt-1 text-xs">{err.hint}</div>}
          </Empty>
        )}
        {plan && (
          <>
            <Panel>
              <PlanView plan={plan} />
              {plan.created && <div className="mt-2 text-[11px] text-ink-3">出方案时间 {plan.created}</div>}
            </Panel>
            <Panel className="flex flex-col gap-3">
              <h2 className="m-0 text-[15px] font-bold">写进游戏</h2>
              <div className="text-xs text-ink-2">游戏先停在首页。先「看差在哪」：只进编成菜单对照，不点格子；确认没问题再「写进游戏」，写完会回读核对。不要选自己常用的编成号。</div>
              <label className="flex items-center gap-2 text-xs text-ink-2">
                写哪个模式的编成
                <select aria-label="模式" className="h-8 rounded-lg border border-line bg-white px-2" value={scenario} onChange={(e) => setScenario(e.target.value)}>
                  {SCENARIOS.map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>
              <div className="flex flex-wrap gap-4">
                <StartTask label="看差在哪（不改）" args={["write-formation", "--scenario", scenario, "--dry-run"]} onOpenJob={onOpenJob} />
                <StartTask label="写进游戏" primary args={["write-formation", "--scenario", scenario]} onOpenJob={onOpenJob} />
              </div>
            </Panel>
          </>
        )}
      </div>
    </div>
  );
}
