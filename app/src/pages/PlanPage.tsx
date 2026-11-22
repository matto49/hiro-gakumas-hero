// 编成方案页：出方案（按社区规则，用信息库里的库存）→ 看理由 → 先看差在哪（只看不改）→ 写进游戏。
import { type ReactNode, useEffect, useState } from "react";
import { PlanView } from "../components/PlanView";
import { StartTask } from "../components/StartTask";
import { Field, Segmented, inputCls } from "../components/Field";
import { IdolPicker, toSkinId } from "../components/IdolPicker";
import { Button, PageTitle } from "../components/ui";
import { HiroError, data, useHiro, type Listing, type Summary } from "../lib/hiro";
import type { PlanSummary } from "../lib/plan";

// 库存超过这么多天算旧：出方案前提示先扫描（新抽的卡、新拿的回忆不在方案里）
const STALE_DAYS = 3;

const INVENTORY: [kind: "supports" | "memories", name: string, unit: string, job: string][] = [
  ["supports", "支援卡", "张", "scan-supports"],
  ["memories", "回忆", "份", "scan-memories"],
];

function daysAgo(at: string): number {
  const t = Date.parse(at.replace(" ", "T"));
  return Number.isNaN(t) ? 0 : Math.floor((Date.now() - t) / 86_400_000);
}

/** 出方案用的库存：各是哪天扫的，旧了提示，旁边是扫描入口（会动游戏，要你确认）。 */
function Inventory({ summary, onOpenJob }: { summary: Summary | null; onOpenJob: (jobId: string) => void }) {
  return (
    <div className="mt-3 flex flex-col gap-2 border-t border-line pt-3">
      <div className="text-[12px] font-medium text-ink-3">用到的库存（游戏停在首页再扫；只读，扫完点キャンセル退出）</div>
      {INVENTORY.map(([kind, name, unit, job]) => {
        const k = summary?.kinds[kind];
        const at = k?.scanned_at ?? null;
        const age = at ? daysAgo(at) : null;
        const stale = age === null || age > STALE_DAYS;
        return (
          <div key={kind} className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="min-w-[180px] text-[13px]">
              {at ? `${name} ${k!.count} ${unit} · ${at.slice(5, 16)} 扫描` : `${name}：还没扫描`}
            </span>
            {stale && (
              <span className="text-[12px] text-warn">
                {at ? `${name}库存是 ${age} 天前扫的，新拿到的不在方案里，建议先扫描` : "出方案前要先扫描"}
              </span>
            )}
            <span className="ml-auto">
              <StartTask label={at ? `重新扫描${name}` : `扫描${name}`} args={[job]} onOpenJob={onOpenJob} />
            </span>
          </div>
        );
      })}
    </div>
  );
}

type SetRow = { mode: string | null; kind: "support" | "memory"; set: number; filled: number | null };
const MODE_LABEL: Record<string, string> = { hif: "H.I.F", hajime: "初", nia: "N.I.A" };

/** 编成号输入框下面的一句：读过编成位就说这个编成号空不空（写进去会覆盖已有的），没读过就是原来的提示。 */
function setHint(rows: SetRow[], kind: "support" | "memory", value: string, fallback: string): ReactNode {
  const n = Number(value);
  const row = rows.find((r) => r.kind === kind && r.set === n);
  if (!value || !row) return fallback;
  const where = row.mode ? `（${MODE_LABEL[row.mode] ?? row.mode} 的编成）` : "";
  if (row.filled === 0) return <span className="text-good">{`编成 ${n} 是空的${where}`}</span>;
  if (row.filled == null) return `编成 ${n} 上次没读出来${where}`;
  return <span className="text-warn">{`编成 ${n} 已有 ${row.filled} 格${where}，写进去会覆盖，确认不是你自己常用的`}</span>;
}

/** 空着的编成号：点一下填进输入框。 */
function EmptySets({ rows, kind, name, onPick }: { rows: SetRow[]; kind: "support" | "memory"; name: string; onPick: (n: number) => void }) {
  const empty = rows.filter((r) => r.kind === kind && r.filled === 0).map((r) => r.set);
  if (!rows.some((r) => r.kind === kind)) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[12px] text-ink-3">
      <span>{empty.length ? `空着的${name}编成：` : `${name}编成没有空的`}</span>
      {empty.slice(0, 8).map((n) => (
        <button key={n} type="button" aria-label={`${name}编成 ${n}`} onClick={() => onPick(n)} className="rounded-md border border-line bg-surface px-1.5 py-0.5 text-[12px] text-ink-2 hover:border-line-strong hover:text-ink">
          {n}
        </button>
      ))}
    </div>
  );
}

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
  const [summary, setSummary] = useState<Summary | null>(null);
  const [sets, setSets] = useState<SetRow[]>([]);

  useEffect(() => {
    data<Summary>(call, ["info", "summary"]).then(setSummary, () => {});
    data<Listing<SetRow>>(call, ["info", "formations"]).then((r) => setSets(r.items ?? []), () => {});
  }, [call]);

  useEffect(() => {
    data<PlanSummary>(call, ["plan", "show"])
      .then((p) => {
        setPlan(p);
        setIdol(p.idol.id ? toSkinId(p.idol.id) : "");
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
    <div className="h-full overflow-auto px-8 py-6">
      <div className="max-w-[820px]">
        <PageTitle sub="按社区攻略，用最近一次扫描的支援卡和回忆库存选：支援卡 5 张 + 租借 1 张、回忆 4 份。只计算，不碰游戏。">开局编成方案</PageTitle>
        <div className="flex flex-col gap-4">
          <section className="rounded-xl border border-line bg-surface p-4">
            <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3">
              <Field label="偶像卡" className="col-span-3 sm:col-span-1">
                <IdolPicker value={idol} onChange={setIdol} initialName={plan?.idol.name} />
              </Field>
              <Field label="支援卡编成号" hint={setHint(sets, "support", sset, "写进游戏用的编成位")}>
                <input aria-label="支援卡编成号" className={inputCls} inputMode="numeric" placeholder="1–20" value={sset} onChange={(e) => setSset(e.target.value.replace(/\D/g, ""))} />
              </Field>
              <Field label="回忆编成号" hint={setHint(sets, "memory", mset, "不要选自己常用的")}>
                <input aria-label="回忆编成号" className={inputCls} inputMode="numeric" placeholder="1–20" value={mset} onChange={(e) => setMset(e.target.value.replace(/\D/g, ""))} />
              </Field>
            </div>
            <div className="mt-2 flex flex-col gap-1">
              <EmptySets rows={sets} kind="support" name="支援卡" onPick={(n) => setSset(String(n))} />
              <EmptySets rows={sets} kind="memory" name="回忆" onPick={(n) => setMset(String(n))} />
            </div>
            <div className="mt-3 flex items-end justify-between gap-3">
              <Segmented name="plan-attr" label="主属性" value={attr} onChange={setAttr} options={[["", "自动"], ["Vo", "Vo"], ["Da", "Da"], ["Vi", "Vi"]]} />
              <Button primary disabled={busy || !idol.trim()} onClick={build}>
                {busy ? "计算中…" : "出方案"}
              </Button>
            </div>
            <Inventory summary={summary} onOpenJob={onOpenJob} />
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="min-w-[180px] text-[13px]">{sets.length ? `编成位 · ${MODE_LABEL[sets[0]!.mode ?? ""] ?? ""} 已读` : "编成位：还没读"}</span>
              <span className="text-[12px] text-ink-3">读一遍 {MODE_LABEL[scenario]} 的 1–20 号，看哪些空着（只翻页，不点格子）</span>
              <span className="ml-auto">
                <StartTask label="读编成位" args={["scan-formations", "--scenario", scenario]} onOpenJob={onOpenJob} />
              </span>
            </div>
          </section>
          {err && (
            <div role="alert" className="rounded-xl border border-line bg-surface-2 px-4 py-3">
              <b className="block text-[13px]">{err.message}</b>
              {err.hint && <div className="mt-0.5 text-[12.5px] text-ink-2">{err.hint}</div>}
            </div>
          )}
          {plan && (
            <>
              <section className="rounded-xl border border-line bg-surface p-4">
                <PlanView plan={plan} />
                {plan.created && <div className="mt-3 text-[11.5px] text-ink-3">出方案时间 {plan.created}</div>}
              </section>
              <section className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
                <h2 className="m-0 text-[15px] font-bold">写进游戏</h2>
                <p className="m-0 text-[12.5px] leading-relaxed text-ink-2">
                  游戏先停在首页。先「看差在哪」：只进编成菜单对照，不点格子；确认没问题再「写进游戏」，写完会回读核对。
                </p>
                <Segmented name="plan-scenario" label="写哪个模式的编成" value={scenario} onChange={setScenario} options={SCENARIOS} />
                <div className="flex flex-wrap gap-4">
                  <StartTask label="看差在哪（不改）" args={["write-formation", "--scenario", scenario, "--dry-run"]} onOpenJob={onOpenJob} />
                  <StartTask label="写进游戏" primary args={["write-formation", "--scenario", scenario]} onOpenJob={onOpenJob} />
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
