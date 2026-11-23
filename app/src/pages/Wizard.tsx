// 新手向导（设计稿 §11）：装环境 → 连游戏 → 关游戏声音 → 配模型 → 存账号密码（可选）→ 首次扫描。
import { useCallback, useEffect, useState } from "react";
import { ConfirmCard } from "../components/Cards";
import { Check as CheckIcon } from "lucide-react";
import { Button, Logo, cx } from "../components/ui";
import { useAssistant } from "../lib/assistant";
import { useBoot, type BootStatus } from "../lib/bootstrap";
import type { Card } from "../lib/chat";
import { cardsFor } from "../lib/quick";
import { data, useHiro } from "../lib/hiro";
import { Check } from "../components/Field";
import { ModelSection, SecretsSection } from "./Settings";

interface Item {
  id: string;
  title: string;
  ok: boolean | null;
  detail: string;
  next: string;
  optional: boolean;
}

const STEPS = ["装环境", "连游戏", "关游戏声音", "配模型", "账号密码", "第一次扫描"];

function ItemRow({ it }: { it: Item | undefined }) {
  if (!it) return null;
  const tone = it.ok === true ? "bg-ok-t text-good" : it.ok === false ? "bg-err-t text-critical" : "bg-warn-t text-warn";
  return (
    <div className="flex gap-3 rounded-lg border border-line bg-surface-2 px-3 py-2.5">
      <span className={cx("grid size-5 flex-none place-items-center rounded-full text-[11px] font-bold", tone)}>{it.ok === true ? "✓" : it.ok === false ? "!" : "?"}</span>
      <div className="min-w-0">
        <b className="text-[13px]">{it.title}</b>
        <div className="text-[12.5px] text-ink-2">{it.detail}</div>
        {it.ok !== true && it.next && <div className="mt-0.5 text-[12px] text-ink-3">{it.next}</div>}
      </div>
    </div>
  );
}

/** 环境没装好时：列出要做的步骤，点「安装」后一行行显示输出。 */
function Install({ status, onInstalled }: { status: BootStatus; onInstalled: () => void }) {
  const boot = useBoot();
  const [lines, setLines] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setRunning(true);
    setErr(null);
    try {
      await boot.run((p) => setLines((ls) => [...ls.slice(-200), p.line]));
      onInstalled();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };
  return (
    <div className="flex flex-col gap-2">
      <div className="text-ink-2">第一次用要先装好初星助手的运行环境（不用你装 Python，全自动）：</div>
      <ul className="m-0 pl-5 text-ink-2">
        {status.steps.map((s) => (
          <li key={s.id}>{s.title}</li>
        ))}
      </ul>
      {status.error && (
        <div className="text-critical">
          {status.error.message}
          {status.error.hint && <div className="text-[12px] text-ink-2">{status.error.hint}</div>}
        </div>
      )}
      <div>
        <Button primary disabled={running || !!status.error} onClick={run}>
          {running ? "安装中…" : "安装"}
        </Button>
      </div>
      {lines.length > 0 && (
        <pre className="m-0 max-h-56 overflow-auto rounded-lg border border-line bg-surface-2 p-2.5 font-mono text-[11px] leading-[1.45] whitespace-pre-wrap text-ink-2">{lines.slice(-40).join("\n")}</pre>
      )}
      {err && <div className="text-[12px] text-critical">{err}</div>}
    </div>
  );
}

export function Wizard({ onDone }: { onDone: () => void }) {
  const call = useHiro();
  const api = useAssistant();
  const boot = useBoot();
  const [bootStatus, setBootStatus] = useState<BootStatus | null>(null);
  const [step, setStep] = useState(0);
  const [items, setItems] = useState<Record<string, Item>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [soundDone, setSoundDone] = useState(false);
  const [modelOk, setModelOk] = useState(false);
  const [scanCards, setScanCards] = useState<Card[]>([]);

  const check = useCallback(
    async (probe = false) => {
      setBusy(probe ? "probe" : "doctor");
      try {
        const r = await data<{ items: Item[] }>(call, probe ? ["doctor", "--probe"] : ["doctor"]);
        setItems(Object.fromEntries(r.items.map((i) => [i.id, i])));
      } catch (e) {
        setNote(String(e));
      } finally {
        setBusy(null);
      }
    },
    [call],
  );
  const recheckBoot = useCallback(async () => {
    const st = await boot.status().catch(() => ({ ready: true, steps: [] }) as BootStatus);
    setBootStatus(st);
    if (st.ready) check();
  }, [boot, check]);
  useEffect(() => {
    recheckBoot();
  }, [recheckBoot]);

  const syncCatalog = async () => {
    setBusy("catalog");
    setNote(null);
    const r = await call(["catalog", "sync"]);
    setNote(r.ok ? "资料库下载好了" : `${r.error?.message}。${r.error?.hint ?? ""}`);
    await check();
  };

  const scan = async (kind: "scan-supports" | "scan-memories" | "scan-account" | "check-settings") => {
    const r = await call(["run", "start", kind]);
    setScanCards((c) => [...c, ...cardsFor(r)]);
  };

  const finish = async () => {
    const s = await api.settings();
    await api.save({ ...s, wizard_done: true, has_key: false }, "");
    onDone();
  };

  const canNext = [items.python?.ok === true, true, soundDone, modelOk, true, true][step];

  return (
    <div className="flex h-full overflow-hidden bg-surface">
      <aside className="flex w-[232px] flex-none flex-col gap-6 border-r border-line bg-surface-2 px-5 py-6">
        <div className="flex items-center gap-2 text-[15px] font-bold">
          <Logo />
          初星助手
        </div>
        <div>
          <div className="mb-2 text-[12px] font-medium text-ink-3">第一次使用 · 六步</div>
          <ol className="m-0 flex list-none flex-col p-0">
            {STEPS.map((s, i) => {
              const done = i < step;
              const now = i === step;
              const last = i === STEPS.length - 1;
              return (
                <li
                  key={s}
                  aria-current={now ? "step" : undefined}
                  className={cx(
                    "relative flex items-center gap-3 rounded-md px-2 py-2 text-[13px] leading-none",
                    now ? "bg-brand-tint font-semibold text-ink" : done ? "text-ink-2" : "text-ink-2",
                  )}
                >
                  {/* 竖线：连到下一步的圆圈；走过的那段用品牌浅色 */}
                  {!last && (
                    <span
                      aria-hidden
                      className={cx("absolute top-[30px] left-[18px] h-[16px] w-[1.5px] rounded-full", done ? "bg-brand/60" : "bg-line-strong")}
                    />
                  )}
                  <span
                    className={cx(
                      "relative z-[1] inline-flex size-[22px] flex-none items-center justify-center rounded-full text-[11.5px] leading-none font-semibold tabular-nums",
                      now
                        ? "bg-brand text-white shadow-[0_0_0_3px_var(--color-brand-soft)]"
                        : done
                          ? "bg-brand-soft text-brand-ink"
                          : "border-[1.5px] border-line-strong bg-surface text-ink-3",
                    )}
                  >
                    {done ? <CheckIcon className="size-3" strokeWidth={3} /> : i + 1}
                  </span>
                  <span className={cx(now ? "" : done ? "" : "text-ink-2")}>{s}</span>
                </li>
              );
            })}
          </ol>
        </div>
        <p className="mt-auto mb-0 text-[11.5px] leading-relaxed text-ink-3">每一步以后都能在「设置」里改。会动游戏的操作都要你点确认。</p>
      </aside>
      <main className="min-w-0 flex-1 overflow-auto px-10 py-8">
      <div className="w-full max-w-[600px]">

        {step === 0 && (
          <section className="flex flex-col gap-3">
            <h2 className="m-0 text-[20px] font-bold tracking-tight">装环境</h2>
            {bootStatus && !bootStatus.ready && <Install status={bootStatus} onInstalled={recheckBoot} />}
            {bootStatus?.ready && !items.python && <div className="text-ink-3">正在检查…（第一次打开要几秒）</div>}
            <ItemRow it={items.python} />
            <ItemRow it={items.catalog} />
            <div className="flex gap-2">
              <Button primary={items.catalog != null && items.catalog.ok !== true} disabled={busy !== null} onClick={syncCatalog}>
                {busy === "catalog" ? "下载中…" : "下载资料库"}
              </Button>
              <Button disabled={busy !== null} onClick={() => check()}>
                重新检查
              </Button>
            </div>
          </section>
        )}

        {step === 1 && (
          <section className="flex flex-col gap-3">
            <h2 className="m-0 text-[20px] font-bold tracking-tight">连上游戏</h2>
            <div className="text-ink-2">先把游戏打开、停在首页。Mac 要用带 maaTools 的 PlayCover（hguandl 分支），并在游戏设置里打开 maaTools；Windows 用 DMM 版，窗口不要最小化。</div>
            <ItemRow it={items.platform} />
            <div>
              <Button disabled={busy !== null} onClick={() => check(true)}>
                试连
              </Button>
            </div>
            <div className="text-[12px] text-ink-3">现在连不上也可以先往下走，之后在设置里重试。</div>
          </section>
        )}

        {step === 2 && (
          <section className="flex flex-col gap-3">
            <h2 className="m-0 text-[20px] font-bold tracking-tight">关掉游戏声音</h2>
            <div className="text-ink-2">初星助手打游戏时不出声：在游戏里打开设置 → 声音，把 BGM、语音、效果音都调到 0。不用动电脑的音量。</div>
            <Check label="我在游戏里关好了" checked={soundDone} onChange={setSoundDone} />
          </section>
        )}

        {step === 3 && (
          <div className="flex flex-col gap-3">
            <ModelSection onChanged={() => {}} onTested={setModelOk} />
            <div className="text-[12px] text-ink-3">填好后点「测试」，成功了才能下一步。不想配也可以跳过：只能用快捷指令。</div>
          </div>
        )}

        {step === 4 && <SecretsSection />}

        {step === 5 && (
          <section className="flex flex-col gap-3">
            <h2 className="m-0 text-[20px] font-bold tracking-tight">第一次扫描账号</h2>
            <div className="text-ink-2">让初星助手读一下首页数值、检查快进设置、翻一遍支援卡和回忆（都只读，不改编成、不改设置）。游戏先停在首页。也可以以后再扫。</div>
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => scan("scan-account")}>读首页数值</Button>
              <Button onClick={() => scan("check-settings")}>检查游戏设置</Button>
              <Button onClick={() => scan("scan-supports")}>扫描支援卡</Button>
              <Button onClick={() => scan("scan-memories")}>扫描回忆</Button>
            </div>
            {scanCards.map((c, i) =>
              c.type === "confirm" ? (
                <ConfirmCard
                  key={i}
                  card={c}
                  onResolved={(r, a) => setScanCards((cs) => cs.map((x, j) => (j === i && x.type === "confirm" ? { ...x, status: a === "cancel" ? "cancelled" : r.ok ? "confirmed" : "failed" } : x)))}
                />
              ) : c.type === "error" ? (
                <div key={i} className="text-[12px] text-critical">
                  {c.error.message}
                </div>
              ) : null,
            )}
          </section>
        )}

        {note && <div className="mt-2 text-xs text-ink-2">{note}</div>}

        <div className="mt-8 flex items-center gap-2 border-t border-line pt-4">
          {step > 0 && <Button onClick={() => setStep(step - 1)}>上一步</Button>}
          <span className="flex-1" />
          {(step === 3 || step === 4) && <Button onClick={() => setStep(step + 1)}>跳过</Button>}
          {step < 5 ? (
            <Button primary disabled={!canNext} onClick={() => setStep(step + 1)}>
              下一步
            </Button>
          ) : (
            <Button primary onClick={finish}>
              完成
            </Button>
          )}
        </div>
      </div>
      </main>
    </div>
  );
}
