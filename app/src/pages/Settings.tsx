// 设置（设计稿 §8、§10）：模型（服务商 + Key + 模型）、账号密码（只由人填写，界面只显示有没有保存）。
import { useEffect, useState } from "react";
import { Choice, Field, inputCls } from "../components/Field";
import { Button, PageTitle, cx } from "../components/ui";
import {
  API_PROVIDERS,
  CLI_PROVIDERS,
  DEFAULT_DEEPSEEK_MODEL,
  isApi,
  isCli,
  providerLabel,
  useAssistant,
  type ApiProvider,
  type Preset,
  type Provider,
  type Settings,
} from "../lib/assistant";
import { messages as t } from "../locales";
import { useBoot } from "../lib/bootstrap";
import { data, useHiro } from "../lib/hiro";

const SECRETS: [string, string][] = [
  ["game-account", "游戏账号"],
  ["game-password", "游戏密码"],
];

const input = inputCls;

const FALLBACK_PRESETS: Preset[] = [
  { id: "deepseek", base_url: "https://api.deepseek.com", default_model: DEFAULT_DEEPSEEK_MODEL, key_url: "https://platform.deepseek.com/api_keys" },
  ...(["kimi", "qwen", "glm", "openai", "openrouter", "custom"] as const).map((id) => ({ id, base_url: "", default_model: "", key_url: "" })),
];

type ModelList = { state: "idle" | "loading" | "ok" | "error"; models: string[]; error?: string };

export function ModelSection({ onChanged, onTested }: { onChanged: () => void; onTested?: (ok: boolean) => void }) {
  const api = useAssistant();
  const [s, setS] = useState<Settings | null>(null);
  const [presets, setPresets] = useState<Preset[]>(FALLBACK_PRESETS);
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [list, setList] = useState<ModelList>({ state: "idle", models: [] });
  // 「高级」：本机命令行；现在用的就是命令行时默认展开
  const [advanced, setAdvanced] = useState(false);
  // 上次用的 API 服务商：从命令行改回 API 时回到它
  const [lastApi, setLastApi] = useState<ApiProvider>("deepseek");
  useEffect(() => {
    api.settings().then((v) => {
      setS(v);
      if (isCli(v.provider)) setAdvanced(true);
      if (isApi(v.provider)) setLastApi(v.provider);
    });
    api.providers?.().then((p) => p?.length && setPresets(p), () => {});
  }, [api]);
  const provider = s?.provider;
  const savedKey = !!(s && isApi(s.provider) && (s.has_keys?.[s.provider] ?? s.has_key));
  const baseURL = s?.base_url ?? "";
  // 填 Key → 自动列模型：已存 Key 的直接列；新填的等停手 0.6 秒再列
  useEffect(() => {
    if (!provider || !isApi(provider) || !api.models || (!savedKey && !key.trim()) || (provider === "custom" && !baseURL.trim())) {
      setList({ state: "idle", models: [] });
      return;
    }
    let live = true;
    const timer = setTimeout(
      () => {
        setList((l) => ({ ...l, state: "loading" }));
        api.models!(provider, baseURL, key.trim()).then(
          (m) => live && setList({ state: "ok", models: m }),
          (e) => live && setList({ state: "error", models: [], error: e instanceof Error ? e.message : String(e) }),
        );
      },
      key.trim() ? 600 : 0,
    );
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [provider, savedKey, key, baseURL, api]);
  if (!s) return null;
  const preset = presets.find((p) => p.id === s.provider);
  const persist = async (next: Settings, k: string) => {
    const cur = await api.settings();
    await api.save({ ...next, wizard_done: cur.wizard_done, has_key: false, has_keys: undefined }, k);
    const fresh = await api.settings();
    setS(fresh);
    onChanged();
    return fresh;
  };
  const save = async () => {
    setBusy(true);
    try {
      await persist(s, key);
      setKey("");
      setMsg({ ok: true, text: t.modelSaved() });
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
      if (key.trim()) {
        await persist(s, key);
        setKey("");
      }
      const r = await api.test();
      setMsg({ ok: r.ok, text: r.message });
      onTested?.(r.ok);
    } finally {
      setBusy(false);
    }
  };
  const pickProvider = (p: Provider) => {
    setKey("");
    setMsg(null);
    const pre = presets.find((x) => x.id === p);
    const model = p === "deepseek" ? DEFAULT_DEEPSEEK_MODEL : pre?.default_model || undefined;
    if (isApi(p)) setLastApi(p);
    setS({ ...s, provider: p, model, base_url: p === "custom" ? s.base_url : undefined, has_key: !!s.has_keys?.[p as ApiProvider] });
  };
  const pickModel = async (m: string) => {
    const next = { ...s, model: m };
    setS(next);
    if (savedKey && !key.trim()) await persist(next, ""); // Key 已存时选了就生效
  };
  const apiOptions = (["none", ...API_PROVIDERS] as Provider[]).map((p) => ({
    value: p,
    label: p === "deepseek" ? t.modelRecommended(providerLabel(p)) : providerLabel(p),
  }));
  const current = s.model || preset?.default_model || "";
  const models = list.models.length && current && !list.models.includes(current) ? [current, ...list.models] : list.models;
  const modelHint =
    list.state === "loading" ? t.modelPickLoading() : list.state === "error" ? t.modelPickFailed(list.error ?? "") : list.state === "ok" ? t.modelPickHint() : t.modelPickNeedKey();
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4">
      <h2 className="m-0 text-[15px] font-bold">{t.modelTitle()}</h2>
      <div className="flex flex-col gap-1 text-[12.5px] leading-relaxed text-ink-2">
        <span>{t.modelUses()}</span>
        <span>{t.modelUsage()}</span>
        <span className="text-ink-3">{t.modelOptional()}</span>
      </div>
      {isCli(s.provider) ? (
        <div className="text-[12.5px] leading-relaxed text-ink-2">{t.modelCliActive(providerLabel(s.provider))}</div>
      ) : (
        <Field as="div" label={t.modelProvider()}>
          <Choice label={t.modelProvider()} value={s.provider} options={apiOptions} onChange={pickProvider} />
        </Field>
      )}
      {isApi(s.provider) && (
        <>
          {s.provider === "custom" && (
            <Field label={t.modelBaseUrl()} hint={t.modelBaseUrlHint()}>
              <input aria-label={t.modelBaseUrl()} className={input} placeholder="https://…/v1" value={s.base_url ?? ""} onChange={(e) => setS({ ...s, base_url: e.target.value })} />
            </Field>
          )}
          <Field
            label={t.modelKey()}
            hint={
              <>
                {savedKey ? t.modelKeySaved() : t.modelKeyMissing()}
                {preset?.key_url ? t.modelKeyWhere(preset.key_url) : null}
              </>
            }
          >
            <input aria-label={t.modelKey()} type="password" autoComplete="off" className={input} placeholder={savedKey ? t.modelKeyPlaceholderSaved() : "sk-…"} value={key} onChange={(e) => setKey(e.target.value)} />
          </Field>
          <Field as={models.length ? "div" : "label"} label={t.modelPick()} hint={modelHint}>
            {models.length ? (
              <Choice
                label={t.modelPick()}
                value={current || null}
                placeholder={t.modelPickPlaceholder()}
                options={models.map((m) => ({ value: m, label: m === preset?.default_model ? t.modelDefaultTag(m) : m }))}
                onChange={pickModel}
              />
            ) : (
              <input aria-label={t.modelPick()} className={input} value={current} onChange={(e) => setS({ ...s, model: e.target.value })} />
            )}
          </Field>
        </>
      )}
      <div className="flex items-center gap-2">
        <Button primary disabled={busy} onClick={save}>
          {t.modelSave()}
        </Button>
        {s.provider !== "none" && (
          <Button disabled={busy} onClick={test}>
            {t.modelTest()}
          </Button>
        )}
        {msg && <span className={cx("text-[12px]", msg.ok ? "text-good" : "text-critical")}>{msg.text}</span>}
      </div>
      <details open={advanced} onToggle={(e) => setAdvanced((e.target as HTMLDetailsElement).open)} className="rounded-lg border border-line bg-surface-2 px-3 py-2">
        <summary className="cursor-pointer text-[12.5px] font-semibold text-ink-2">{t.modelAdvanced()}</summary>
        <div className="mt-2 flex flex-col gap-2 text-[12.5px] leading-relaxed text-ink-2">
          <span>{t.modelAdvancedHint()}</span>
          <div className="flex flex-wrap gap-2">
            {CLI_PROVIDERS.map((p) => (
              <Button key={p} primary={s.provider === p} onClick={() => pickProvider(p)}>
                {t.modelUseCli(providerLabel(p))}
              </Button>
            ))}
            {isCli(s.provider) && <Button onClick={() => pickProvider(lastApi)}>{t.modelUseApi()}</Button>}
          </div>
        </div>
      </details>
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
        游戏掉登录时，初星助手会把账号、密码依次复制到剪贴板，粘贴进登录框（45 秒后自动清空）。存在系统钥匙串里，界面和初星助手都看不到明文。
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
      <div className="text-[12.5px] leading-relaxed text-ink-2">初星助手的运行环境（Python、依赖、游戏数据）。出错卡让你重新安装时点这里；已经装好的部分会跳过，一般几分钟。</div>
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
      <PageTitle sub="模型、游戏账号和运行环境。模型 Key 存在本机数据目录（只有你能读），游戏账号密码存在系统钥匙串里，界面上都看不到明文。">设置</PageTitle>
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
