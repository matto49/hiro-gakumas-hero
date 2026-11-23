// 开局页（设计简报 §5）：五种模式一行一个，点开展开参数，下面内联确认（不弹窗）。参数缺省按编成方案。
import { ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { Check, Choice, Field, Segmented, inputCls } from "../components/Field";
import { IdolPicker } from "../components/IdolPicker";
import { StartTask } from "../components/StartTask";
import { PageTitle, cx } from "../components/ui";
import { data, useHiro } from "../lib/hiro";
import type { PlanSummary } from "../lib/plan";

type Mode = "hif" | "hajime" | "nia" | "tower" | "daily";

const MODES: { id: Mode; name: string; ja: string; desc: string }[] = [
  { id: "hif", name: "H.I.F", ja: "選抜 / 本戦", desc: "选拔打完拿回忆，本战冲名次。开本战会用掉一份选拔回忆。" },
  { id: "hajime", name: "初", ja: "はじめ", desc: "定期公演「初」，REGULAR / PRO / MASTER。" },
  { id: "nia", name: "N.I.A", ja: "エヌアイエー", desc: "PRO / MASTER。认不出的页面会停下来等你处理。" },
  { id: "tower", name: "偶像之路", ja: "アイドルへの道", desc: "按偶像路线往上爬，三星优先。" },
  { id: "daily", name: "日常", ja: "デイリー", desc: "活動費、礼物、工作、竞赛、任务奖励、社团，不花ジュエル。" },
];

const DAILY: [string, string][] = [
  ["activity_funds", "活動費"],
  ["presents", "礼物"],
  ["assignment", "工作"],
  ["contest", "竞赛"],
  ["mission_reward", "任务奖励"],
  ["club_reward", "社团"],
];

// 主属性：「按编成方案」是空值（下拉框的值不能是空串，用 plan 代替）
const ATTR_OPTS = [
  { value: "plan", label: "按编成方案" },
  { value: "Vo", label: "Vo" },
  { value: "Da", label: "Da" },
  { value: "Vi", label: "Vi" },
];

function ProduceForm({ mode, plan, onArgs }: { mode: "hif" | "hajime" | "nia"; plan: PlanSummary | null; onArgs: (a: string[] | null) => void }) {
  const [resume, setResume] = useState(false);
  const [hifMode, setHifMode] = useState<"qualify" | "main">("qualify");
  const [difficulty, setDifficulty] = useState(mode === "hajime" ? "regular" : "pro");
  const [idol, setIdol] = useState("");
  const [attr, setAttr] = useState("");
  const [sset, setSset] = useState("");
  const [mset, setMset] = useState("");
  useEffect(() => {
    if (!resume && !idol.trim()) return onArgs(null);
    const a = [mode, "--command", resume ? "resume" : "new"];
    if (mode === "hif") a.push("--mode", hifMode);
    if (!resume) a.push("--idol", idol.trim());
    if (mode !== "hif") a.push("--difficulty", difficulty);
    if (!resume) {
      if (attr) a.push("--main-attr", attr);
      if (sset) a.push("--support-set", sset);
      if (mset) a.push("--memory-set", mset);
    }
    onArgs(a);
  }, [mode, resume, hifMode, difficulty, idol, attr, sset, mset, onArgs]);
  const planHint = plan ? `不填按编成方案（${plan.idol.name ?? "?"}：支援卡 ${plan.support.set ?? "?"} 号、回忆 ${plan.memory.set ?? "?"} 号）` : "不填就用游戏里当前选中的编成";
  return (
    <div className="flex flex-col gap-4">
      <Check label="接着打进行中的培育" checked={resume} onChange={setResume} hint="游戏里已经有一局开着时用" />
      <div className={cx("flex flex-wrap gap-x-6 gap-y-3", resume && "opacity-60")}>
        {mode === "hif" && (
          <Segmented name={`${mode}-mode`} label="打哪一段" value={hifMode} onChange={setHifMode} options={[["qualify", "选拔"], ["main", "本战"]]} />
        )}
        {mode !== "hif" && (
          <Segmented
            name={`${mode}-difficulty`}
            label="难度"
            value={difficulty}
            onChange={setDifficulty}
            options={(mode === "hajime" ? [["regular", "REGULAR"], ["pro", "PRO"], ["master", "MASTER"]] : [["pro", "PRO"], ["master", "MASTER"]]) as [string, string][]}
          />
        )}
      </div>
      {!resume && (
        <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3">
          <Field label="偶像卡" hint={plan?.idol.name ? `编成方案是按「${plan.idol.name}」出的` : "搜卡名，也可以直接填编号"}>
            <IdolPicker value={idol} onChange={setIdol} />
          </Field>
          <Field as="div" label="主属性">
            <Choice label="主属性" value={attr || "plan"} options={ATTR_OPTS} onChange={(v) => setAttr(v === "plan" ? "" : v)} />
          </Field>
          <Field label="支援卡编成号">
            <input aria-label="支援卡编成号" inputMode="numeric" className={inputCls} value={sset} onChange={(e) => setSset(e.target.value.replace(/\D/g, ""))} placeholder="1–20" />
          </Field>
          <Field label="回忆编成号">
            <input aria-label="回忆编成号" inputMode="numeric" className={inputCls} value={mset} onChange={(e) => setMset(e.target.value.replace(/\D/g, ""))} placeholder="1–20" />
          </Field>
          <p className="col-span-4 m-0 -mt-1 text-[11.5px] text-ink-3">{planHint}</p>
        </div>
      )}
      {mode === "hif" && hifMode === "main" && !resume && <p className="m-0 text-[12px] text-warn">开本战会用掉一份选拔回忆。</p>}
    </div>
  );
}

function TowerForm({ onArgs }: { onArgs: (a: string[] | null) => void }) {
  const [idol, setIdol] = useState("");
  const [layers, setLayers] = useState("1");
  useEffect(() => {
    onArgs(idol.trim() ? ["tower", "--idol", idol.trim(), "--layers", layers || "1"] : null);
  }, [idol, layers, onArgs]);
  return (
    <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-3">
      <Field label="路线（偶像名）" hint="画面上的写法，例如 篠澤広">
        <input aria-label="路线（偶像名）" className={inputCls} value={idol} onChange={(e) => setIdol(e.target.value)} />
      </Field>
      <Field label="打几层" hint="从当前最前沿往上">
        <input aria-label="打几层" inputMode="numeric" className={inputCls} value={layers} onChange={(e) => setLayers(e.target.value.replace(/\D/g, ""))} />
      </Field>
    </div>
  );
}

function DailyForm({ onArgs }: { onArgs: (a: string[] | null) => void }) {
  const [picked, setPicked] = useState(() => new Set(DAILY.map(([k]) => k)));
  useEffect(() => {
    const items = DAILY.map(([k]) => k).filter((k) => picked.has(k));
    if (!items.length) return onArgs(null);
    onArgs(items.length === DAILY.length ? ["daily"] : ["daily", "--items", items.join(",")]);
  }, [picked, onArgs]);
  return (
    <div className="grid grid-cols-3 gap-x-4 gap-y-2">
      {DAILY.map(([k, label]) => (
        <Check
          key={k}
          label={label}
          checked={picked.has(k)}
          onChange={(on) =>
            setPicked((s) => {
              const n = new Set(s);
              if (on) n.add(k);
              else n.delete(k);
              return n;
            })
          }
        />
      ))}
    </div>
  );
}

export function StartPage({ onOpenJob }: { onOpenJob: (jobId: string) => void }) {
  const call = useHiro();
  const [open, setOpen] = useState<Mode | null>(null);
  const [args, setArgs] = useState<string[] | null>(null);
  const [plan, setPlan] = useState<PlanSummary | null>(null);
  useEffect(() => {
    data<PlanSummary>(call, ["plan", "show"]).then(setPlan, () => setPlan(null));
  }, [call]);
  const toggle = (m: Mode) => {
    setArgs(null);
    setOpen((o) => (o === m ? null : m));
  };
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto flex max-w-[780px] flex-col gap-1 px-8 py-7">
        <PageTitle sub="选一个模式，填好就让初星助手去打。会动游戏的操作都要你点一下确认；花ジュエル、放弃培育的事它做不了。">开局</PageTitle>
        <ul className="m-0 list-none divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface p-0">
          {MODES.map((m) => {
            const on = open === m.id;
            return (
              <li key={m.id}>
                <button
                  type="button"
                  aria-expanded={on}
                  onClick={() => toggle(m.id)}
                  className={cx("flex w-full items-center gap-4 px-5 py-3.5 text-left transition-colors", on ? "bg-brand-tint" : "hover:bg-surface-2")}
                >
                  <span className="w-[76px] flex-none text-[15px] font-bold">{m.name}</span>
                  <span className="min-w-0 flex-1">
                    <span className="jp block text-[11px] text-ink-3">{m.ja}</span>
                    <span className="block truncate text-ink-2">{m.desc}</span>
                  </span>
                  <ChevronRight className={cx("size-4 flex-none text-ink-3 transition-transform duration-200", on && "rotate-90 text-brand-ink")} />
                </button>
                {on && (
                  <div className="flex flex-col gap-4 border-t border-line bg-surface px-5 pt-4 pb-5">
                    {(m.id === "hif" || m.id === "hajime" || m.id === "nia") && <ProduceForm mode={m.id} plan={plan} onArgs={setArgs} />}
                    {m.id === "tower" && <TowerForm onArgs={setArgs} />}
                    {m.id === "daily" && <DailyForm onArgs={setArgs} />}
                    <StartTask key={(args ?? []).join(" ")} label="准备开局" primary disabled={!args} args={args ?? []} onOpenJob={onOpenJob} />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        <p className="m-0 mt-3 text-[12px] text-ink-3">开局前让游戏停在首页。同一时间只跑一个任务。</p>
      </div>
    </div>
  );
}
