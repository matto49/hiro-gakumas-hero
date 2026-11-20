// 设置（设计稿 §8、§10）：模型（服务商 + Key + 模型）、账号密码（只由人填写，界面只显示有没有保存）。
import { useEffect, useState } from "react";
import { Field, inputCls, selectCls, selectStyle } from "../components/Field";
import { Button, PageTitle, cx } from "../components/ui";
import { DEFAULT_DEEPSEEK_MODEL, PROVIDER_LABEL, useAssistant, type Settings } from "../lib/assistant";
import { useBoot } from "../lib/bootstrap";
import { data, useHiro } from "../lib/hiro";

const SECRETS: [string, string][] = [
  ["game-account", "游戏账号"],
  ["game-password", "游戏密码"],
];

const input = inputCls;

export function ModelSection({ onChanged, onTested }: { onChanged: () => void; onTested?: (ok: boolean) => void }) {
  const api = useAssistant();
  const [s, setS] = useState<Settings | null>(null);
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  useEffect(() => {
    api.settings().then(setS);
  }, [api]);
  const hasKey = s?.provider === "deepseek" && s.has_key;
  useEffect(() => {
    if (!hasKey || !api.models) {
      setModels([]);
      return;
    }
    let live = true;
    api.models().then(
      (m) => live && setModels(m),
      () => live && setModels([]),
    );
    return () => {
      live = false;
    };
  }, [hasKey, api]);
  if (!s) return null;
  const save = async () => {
    setBusy(true);
    try {
      const cur = await api.settings();
      await api.save({ ...s, wizard_done: cur.wizard_done, has_key: false }, key);
      setKey("");
      setS(await api.settings());
      setMsg({ ok: true, text: "已保存" });
      onChanged();
    } catch (e) {
      setMsg({ ok: false, text: String(e) });
    } finally {
      setBusy(false);
    }
  };
  const test = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.test();
      setMsg({ ok: r.ok, text: r.message });
      onTested?.(r.ok);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
      <h2 className="m-0 text-[15px] font-bold">模型</h2>
      <div className="text-[12.5px] leading-relaxed text-ink-2">不配模型也能用：只能点快捷指令，对局里的决策只用规则和算法。</div>
      <Field label="服务商">
        <select aria-label="服务商" className={selectCls} style={selectStyle} value={s.provider} onChange={(e) => setS({ ...s, provider: e.target.value as Settings["provider"] })}>
          {(Object.keys(PROVIDER_LABEL) as Settings["provider"][]).map((p) => (
            <option key={p} value={p}>
              {p === "deepseek" ? "DeepSeek（推荐）" : PROVIDER_LABEL[p]}
            </option>
          ))}
        </select>
      </Field>
      {s.provider === "deepseek" && (
        <>
          <Field
            label="API Key"
            hint={
              <>
                {s.has_key ? "Key 已保存（存在系统钥匙串里，这里不显示）；要换就填新的。" : "还没填。"}
                到 platform.deepseek.com 登录 → API keys → 创建，复制过来粘贴。
              </>
            }
          >
            <input aria-label="API Key" type="password" autoComplete="off" className={input} placeholder={s.has_key ? "已保存" : "sk-…"} value={key} onChange={(e) => setKey(e.target.value)} />
          </Field>
          <Field label="模型" hint={models.length ? "列表是这个 Key 能用的模型，一般用默认的 deepseek-flash" : "一般不用改"}>
            {models.length ? (
              <select aria-label="模型" className={selectCls} style={selectStyle} value={s.model ?? DEFAULT_DEEPSEEK_MODEL} onChange={(e) => setS({ ...s, model: e.target.value })}>
                {(models.includes(s.model ?? DEFAULT_DEEPSEEK_MODEL) ? models : [s.model ?? DEFAULT_DEEPSEEK_MODEL, ...models]).map((m) => (
                  <option key={m} value={m}>
                    {m === DEFAULT_DEEPSEEK_MODEL ? `${m}（默认）` : m}
                  </option>
                ))}
              </select>
            ) : (
              <input aria-label="模型" className={input} value={s.model ?? DEFAULT_DEEPSEEK_MODEL} onChange={(e) => setS({ ...s, model: e.target.value })} />
            )}
          </Field>
        </>
      )}
      {(s.provider === "codex" || s.provider === "claude") && (
        <div className="text-[12.5px] leading-relaxed text-ink-2">
          用这台电脑上已经登录的 {s.provider} 命令行。它会按说明自己调用小广的工具；开局一样要你点确认卡。
        </div>
      )}
      <div className="flex items-center gap-2">
        <Button primary disabled={busy} onClick={save}>
          保存
        </Button>
        {s.provider !== "none" && (
          <Button disabled={busy} onClick={test}>
            测试
          </Button>
        )}
        {msg && <span className={cx("text-[12px]", msg.ok ? "text-good" : "text-critical")}>{msg.text}</span>}
      </div>
    </section>
  );
}

function SecretRow({ name, label, saved, onSaved }: { name: string; label: string; saved: boolean; onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    if (!value) return;
    const { Hiro } = await import("../mygo");
    const r = await Hiro.setSecret(name, value);
    setValue("");
    if (!r.ok) setErr(r.error?.message ?? "没存上");
    else {
      setErr(null);
      onSaved();
    }
  };
  return (
    <div className="flex items-center gap-2">
      <span className="w-20 text-ink-2">{label}</span>
      <span className={cx("w-14 text-[12px]", saved ? "text-good" : "text-ink-3")}>{saved ? "已保存" : "没保存"}</span>
      <input type="password" autoComplete="off" className={cx(input, "flex-1")} placeholder={saved ? "要换就填新的" : `填${label}`} value={value} onChange={(e) => setValue(e.target.value)} />
      <Button disabled={!value} onClick={save} aria-label={`保存${label}`}>
        保存
      </Button>
      {err && <span className="text-[12px] text-critical">{err}</span>}
    </div>
  );
}

export function SecretsSection() {
  const call = useHiro();
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const load = () =>
    data<{ secrets: { name: string }[] }>(call, ["secret", "list"])
      .then((r) => setSaved(new Set(r.secrets.map((x) => x.name))))
      .catch(() => {});
  useEffect(() => {
    load();
  }, [call]);
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
      <h2 className="m-0 text-[15px] font-bold">游戏账号密码（可选）</h2>
      <div className="text-[12.5px] leading-relaxed text-ink-2">
        游戏掉登录时，小广会把账号、密码依次复制到剪贴板，粘贴进登录框（45 秒后自动清空）。存在系统钥匙串里，界面和小广都看不到明文。
      </div>
      {SECRETS.map(([name, label]) => (
        <SecretRow key={name} name={name} label={label} saved={saved.has(name)} onSaved={load} />
      ))}
    </section>
  );
}

function EnvSection() {
  const boot = useBoot();
  const [lines, setLines] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const reinstall = async () => {
    setRunning(true);
    setMsg(null);
    setLines([]);
    try {
      await boot.reset?.();
      await boot.run((p) => setLines((ls) => [...ls.slice(-200), p.line]));
      setMsg({ ok: true, text: "装好了" });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setRunning(false);
    }
  };
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
      <h2 className="m-0 text-[15px] font-bold">环境</h2>
      <div className="text-[12.5px] leading-relaxed text-ink-2">小广的运行环境（Python、依赖、游戏数据）。出错卡让你重新安装时点这里；已经装好的部分会跳过，一般几分钟。</div>
      <div className="flex items-center gap-2">
        <Button disabled={running} onClick={reinstall}>
          {running ? "安装中…" : "重新安装"}
        </Button>
        {msg && <span className={cx("text-[12px]", msg.ok ? "text-good" : "text-critical")}>{msg.text}</span>}
      </div>
      {lines.length > 0 && (
        <pre className="m-0 max-h-56 overflow-auto rounded-lg border border-line bg-surface-2 p-2.5 font-mono text-[11px] leading-[1.45] whitespace-pre-wrap text-ink-2">{lines.slice(-40).join("\n")}</pre>
      )}
    </section>
  );
}

function RedoWizard({ onChanged }: { onChanged: () => void }) {
  const api = useAssistant();
  return (
    <div>
      <button
        type="button"
        className="text-[12.5px] font-semibold text-brand-deep hover:underline"
        onClick={async () => {
          const s = await api.settings();
          await api.save({ ...s, wizard_done: false, has_key: false }, "");
          onChanged();
        }}
      >
        重新走一遍新手向导 ›
      </button>
    </div>
  );
}

export function SettingsPage({ onChanged }: { onChanged: () => void }) {
  return (
    <div className="h-full overflow-auto px-8 py-6">
      <div className="max-w-[640px]">
      <PageTitle sub="模型、游戏账号和运行环境。Key 和密码存在系统钥匙串里，界面上看不到明文。">设置</PageTitle>
      <div className="flex flex-col gap-4">
        <ModelSection onChanged={onChanged} />
        <SecretsSection />
        <EnvSection />
        <RedoWizard onChanged={onChanged} />
      </div>
      </div>
    </div>
  );
}
