// 设置（设计稿 §8、§10）：模型（服务商 + Key + 模型）、账号密码（只由人填写，界面只显示有没有保存）。
import { useEffect, useState } from "react";
import { Button, Panel, cx } from "../components/ui";
import { PROVIDER_LABEL, useAssistant, type Settings } from "../lib/assistant";
import { data, useHiro } from "../lib/hiro";

const SECRETS: [string, string][] = [
  ["game-account", "游戏账号"],
  ["game-password", "游戏密码"],
];

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-ink-2">{label}</span>
      {children}
      {hint && <span className="text-[11.5px] text-ink-3">{hint}</span>}
    </label>
  );
}

const input = "h-9 rounded-lg border border-line bg-white px-3 outline-none focus:border-acc";

function ModelSection({ onChanged }: { onChanged: () => void }) {
  const api = useAssistant();
  const [s, setS] = useState<Settings | null>(null);
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.settings().then(setS);
  }, [api]);
  if (!s) return null;
  const save = async () => {
    setBusy(true);
    try {
      await api.save({ ...s, has_key: false }, key);
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
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel className="flex flex-col gap-3">
      <h2 className="m-0 text-[15px] font-bold">模型</h2>
      <div className="text-xs text-ink-2">不配模型也能用：只能点快捷指令，对局里的决策只用规则和算法。</div>
      <Field label="服务商">
        <select aria-label="服务商" className={input} value={s.provider} onChange={(e) => setS({ ...s, provider: e.target.value as Settings["provider"] })}>
          {(Object.keys(PROVIDER_LABEL) as Settings["provider"][]).map((p) => (
            <option key={p} value={p}>
              {p === "" ? "不用模型" : PROVIDER_LABEL[p]}
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
          <Field label="模型" hint="一般不用改">
            <input className={input} value={s.model ?? "deepseek-chat"} onChange={(e) => setS({ ...s, model: e.target.value })} />
          </Field>
        </>
      )}
      {(s.provider === "codex" || s.provider === "claude") && (
        <div className="text-xs text-ink-2">
          用这台电脑上已经登录的 {s.provider} 命令行。它会按说明自己调用小广的工具；开局一样要你点确认卡。
        </div>
      )}
      <div className="flex items-center gap-2">
        <Button primary disabled={busy} onClick={save}>
          保存
        </Button>
        {s.provider !== "" && (
          <Button disabled={busy} onClick={test}>
            测试
          </Button>
        )}
        {msg && <span className={cx("text-xs", msg.ok ? "text-ok" : "text-err")}>{msg.text}</span>}
      </div>
    </Panel>
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
      <span className={cx("w-14 text-xs", saved ? "text-ok" : "text-ink-3")}>{saved ? "已保存" : "没保存"}</span>
      <input type="password" autoComplete="off" className={cx(input, "flex-1")} placeholder={saved ? "要换就填新的" : `填${label}`} value={value} onChange={(e) => setValue(e.target.value)} />
      <Button disabled={!value} onClick={save} aria-label={`保存${label}`}>
        保存
      </Button>
      {err && <span className="text-xs text-err">{err}</span>}
    </div>
  );
}

function SecretsSection() {
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
    <Panel className="flex flex-col gap-3">
      <h2 className="m-0 text-[15px] font-bold">游戏账号密码（可选）</h2>
      <div className="text-xs text-ink-2">
        游戏掉登录时，小广会把账号、密码依次复制到剪贴板，粘贴进登录框（45 秒后自动清空）。存在系统钥匙串里，界面和小广都看不到明文。
      </div>
      {SECRETS.map(([name, label]) => (
        <SecretRow key={name} name={name} label={label} saved={saved.has(name)} onSaved={load} />
      ))}
    </Panel>
  );
}

export function SettingsPage({ onChanged }: { onChanged: () => void }) {
  return (
    <div className="h-full overflow-auto px-6 py-5">
      <h1 className="m-0 mb-4 text-xl font-bold">设置</h1>
      <div className="flex max-w-[640px] flex-col gap-4">
        <ModelSection onChanged={onChanged} />
        <SecretsSection />
      </div>
    </div>
  );
}
