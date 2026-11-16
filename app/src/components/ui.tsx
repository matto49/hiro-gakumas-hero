import type { ButtonHTMLAttributes, ReactNode } from "react";

export function cx(...a: (string | false | null | undefined)[]): string {
  return a.filter(Boolean).join(" ");
}

export function Button({ primary, className, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { primary?: boolean }) {
  return (
    <button
      type="button"
      {...p}
      className={cx(
        "inline-flex h-[30px] items-center justify-center rounded-[7px] border px-3 text-[12.5px] font-bold transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        primary ? "border-acc bg-acc text-white hover:brightness-105" : "border-line bg-white text-ink hover:bg-chip",
        className,
      )}
    />
  );
}

export function Chip({ children, on, onClick, className }: { children: ReactNode; on?: boolean; onClick?: () => void; className?: string }) {
  const cls = cx(
    "inline-flex h-[26px] items-center gap-1.5 rounded-full border px-2.5 text-xs",
    on ? "border-ink bg-ink text-white" : "border-line bg-white text-ink-2",
    onClick && "cursor-pointer hover:border-ink-3",
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

export function Dot({ tone = "ok" }: { tone?: "ok" | "acc" | "warn" | "off" }) {
  const c = { ok: "bg-ok", acc: "bg-acc", warn: "bg-warn", off: "bg-ink-3" }[tone];
  return <span className={cx("inline-block size-[7px] rounded-full", c)} />;
}

export function SourceTag({ label }: { label: string }) {
  return <span className="ml-1.5 inline-block rounded border border-line px-1.5 text-[10.5px] font-normal leading-[18px] text-ink-3">{label}</span>;
}

export function Panel({ children, className, accent }: { children: ReactNode; className?: string; accent?: boolean }) {
  return <div className={cx("rounded-xl border p-3", accent ? "border-[1.5px] border-acc bg-[#fff8f4]" : "border-line bg-panel", className)}>{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-line p-6 text-center text-ink-3">{children}</div>;
}
