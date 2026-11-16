// 新手向导（设计稿 §11）：装环境 → 连游戏 → 关游戏声音 → 配模型 → 存账号密码（可选）→ 首次扫描。
import { useCallback, useEffect, useState } from "react";
import { ConfirmCard } from "../components/Cards";
import { Button, Panel, cx } from "../components/ui";
import { useAssistant } from "../lib/assistant";
import type { Card } from "../lib/chat";
import { cardsFor } from "../lib/quick";
import { data, useHiro } from "../lib/hiro";
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
  const tone = it.ok === true ? "text-ok" : it.ok === false ? "text-err" : "text-warn";
  return (
    <div className="flex gap-2.5">
      <span className={cx("w-4 flex-none font-bold", tone)}>{it.ok === true ? "✓" : it.ok === false ? "!" : "?"}</span>
      <div>
        <b>{it.title}</b>
        <div className="text-ink-2">{it.detail}</div>
        {it.ok !== true && it.next && <div className="text-xs text-ink-3">{it.next}</div>}
      </div>
    </div>
  );
}

export function Wizard({ onDone }: { onDone: () => void }) {
  const call = useHiro();
  const api = useAssistant();
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
  useEffect(() => {
    check();
  }, [check]);

  const syncCatalog = async () => {
    setBusy("catalog");
    setNote(null);
    const r = await call(["catalog", "sync"]);
    setNote(r.ok ? "资料库下载好了" : `${r.error?.message}。${r.error?.hint ?? ""}`);
    await check();
  };

  const scan = async (kind: "scan-supports" | "scan-memories") => {
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
    <div className="flex h-full items-start justify-center overflow-auto bg-bg px-6 py-10">
      <div className="w-full max-w-[640px]">
        <div className="mb-1 flex items-center gap-2 text-[15px] font-bold">
          <i className="inline-block size-[22px] rounded-md bg-acc" />
          数字小广 · 第一次使用
        </div>
        <ol className="mb-5 flex flex-wrap gap-1.5 p-0 text-xs">
          {STEPS.map((s, i) => (
            <li key={s} className={cx("list-none rounded-full px-2.5 py-0.5", i === step ? "bg-ink text-white" : i < step ? "bg-ok-t text-ok" : "bg-chip text-ink-3")}>
              {i + 1}. {s}
            </li>
          ))}
        </ol>

        {step === 0 && (
          <Panel className="flex flex-col gap-3">
            <h2 className="m-0 text-[17px] font-bold">装环境</h2>
            {!items.python && <div className="text-ink-3">正在检查…（第一次打开要几秒）</div>}
            <ItemRow it={items.python} />
            <ItemRow it={items.catalog} />
            <div className="flex gap-2">
              <Button disabled={busy !== null} onClick={syncCatalog}>
                {busy === "catalog" ? "下载中…" : "下载资料库"}
              </Button>
              <Button disabled={busy !== null} onClick={() => check()}>
                重新检查
              </Button>
            </div>
          </Panel>
        )}

        {step === 1 && (
          <Panel className="flex flex-col gap-3">
            <h2 className="m-0 text-[17px] font-bold">连上游戏</h2>
            <div className="text-ink-2">先把游戏打开、停在首页。Mac 要用带 maaTools 的 PlayCover（hguandl 分支），并在游戏设置里打开 maaTools；Windows 用 DMM 版，窗口不要最小化。</div>
            <ItemRow it={items.platform} />
            <div>
              <Button disabled={busy !== null} onClick={() => check(true)}>
                试连
              </Button>
            </div>
            <div className="text-xs text-ink-3">现在连不上也可以先往下走，之后在设置里重试。</div>
          </Panel>
        )}

        {step === 2 && (
          <Panel className="flex flex-col gap-3">
            <h2 className="m-0 text-[17px] font-bold">关掉游戏声音</h2>
            <div className="text-ink-2">小广打游戏时不出声：在游戏里打开设置 → 声音，把 BGM、语音、效果音都调到 0。不用动电脑的音量。</div>
            <label className="flex items-center gap-2">
              <input type="checkbox" aria-label="我在游戏里关好了" checked={soundDone} onChange={(e) => setSoundDone(e.target.checked)} />
              我在游戏里关好了
            </label>
          </Panel>
        )}

        {step === 3 && (
          <div className="flex flex-col gap-3">
            <ModelSection onChanged={() => {}} onTested={setModelOk} />
            <div className="text-xs text-ink-3">填好后点「测试」，成功了才能下一步。不想配也可以跳过：只能用快捷指令。</div>
          </div>
        )}

        {step === 4 && <SecretsSection />}

        {step === 5 && (
          <Panel className="flex flex-col gap-3">
            <h2 className="m-0 text-[17px] font-bold">第一次扫描账号</h2>
            <div className="text-ink-2">让小广在游戏里翻一遍支援卡和回忆（只读，不改编成）。游戏先停在首页。也可以以后再扫。</div>
            <div className="flex gap-2">
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
                <div key={i} className="text-xs text-err">
                  {c.error.message}
                </div>
              ) : null,
            )}
          </Panel>
        )}

        {note && <div className="mt-2 text-xs text-ink-2">{note}</div>}

        <div className="mt-4 flex items-center gap-2">
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
    </div>
  );
}
