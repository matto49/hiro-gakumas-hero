// 项目自己的小组件。按钮用攻略站搬来的 shadcn Button（胶囊、深橙主按钮带一道底影，像游戏按钮）。
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Button as UIButton } from "./ui/button";

export function cx(...a: (string | false | null | undefined)[]): string {
  return a.filter(Boolean).join(" ");
}

export function Button({ primary, className, children, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { primary?: boolean }) {
  return (
    <UIButton type="button" variant={primary ? "default" : "outline"} size="sm" className={className} {...(p as object)}>
      {children}
    </UIButton>
  );
}

export function Chip({ children, on, onClick, className }: { children: ReactNode; on?: boolean; onClick?: () => void; className?: string }) {
  const cls = cx(
    "inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-[12px] transition-colors",
    on ? "border-ink bg-ink font-semibold text-surface" : "border-line-strong bg-surface text-ink-2",
    onClick && !on && "cursor-pointer hover:border-brand hover:text-brand-ink",
    className,
  );
  return onClick ? (
    <button type="button" className={cls} onClick={onClick} aria-pressed={on}>
      {children}
    </button>
  ) : (
    <span className={cls}>{children}</span>
  );
}

export function Dot({ tone = "ok", pulse }: { tone?: "ok" | "acc" | "warn" | "off" | "err"; pulse?: boolean }) {
  const c = { ok: "bg-good", acc: "bg-brand", warn: "bg-warning", off: "bg-ink-3", err: "bg-critical" }[tone];
  return (
    <span className="relative inline-flex size-[7px] flex-none">
      {pulse && <span className={cx("absolute inset-0 animate-ping rounded-full opacity-60 motion-reduce:hidden", c)} />}
      <span className={cx("relative inline-block size-[7px] rounded-full", c)} />
    </span>
  );
}

/** 决策出处（规则 / 算法 / LLM / 兜底）：小号描边标签，颜色只是辅助。 */
export function SourceTag({ label, className }: { label: string; className?: string }) {
  const tone =
    label === "LLM" ? "border-da/40 text-da-strong" : label === "算法" ? "border-brand/50 text-brand-ink" : label === "兜底" ? "border-critical/40 text-critical" : "border-line-strong text-ink-3";
  return <span className={cx("inline-block flex-none whitespace-nowrap rounded-[4px] border px-1.5 text-[10.5px] leading-[17px] font-medium", tone, className)}>{label}</span>;
}

export function Panel({ children, className, accent }: { children: ReactNode; className?: string; accent?: boolean }) {
  return (
    <div className={cx("rounded-xl border p-3.5", accent ? "border-brand bg-brand-tint shadow-[0_0_0_3px_var(--color-brand-soft)]" : "border-line bg-surface", className)}>
      {children}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-line-strong bg-surface-2 p-6 text-center text-ink-2">{children}</div>;
}

/** 标志：Vo 粉、Da 蓝、Vi 黄三道小斜杠（同初星攻略站顶栏）。 */
export function Logo({ size = 18 }: { size?: number }) {
  const h = size;
  return (
    <span className="inline-flex items-end gap-[2px]" aria-hidden style={{ height: h }}>
      {["bg-vo", "bg-da", "bg-vi"].map((c, i) => (
        <i key={c} className={cx("block w-[4px] -skew-x-[16deg] rounded-[1.5px]", c)} style={{ height: [0.8, 1, 0.7][i]! * h }} />
      ))}
    </span>
  );
}

/** 页标题：左边一道斜的橙色标记（攻略站 SectionTitle 的写法）。 */
export function PageTitle({ children, sub, right }: { children: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-5 flex items-end gap-3">
      <div className="min-w-0 flex-1">
        <h1 className="m-0 flex items-center gap-2 text-[21px] font-bold tracking-tight">
          <i className="inline-block h-[18px] w-[5px] -skew-x-[16deg] rounded-[1.5px] bg-brand" aria-hidden />
          {children}
        </h1>
        {sub && <p className="m-0 mt-1 text-ink-2">{sub}</p>}
      </div>
      {right}
    </div>
  );
}
