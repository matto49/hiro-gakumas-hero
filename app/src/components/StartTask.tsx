// 发起一个会动游戏的任务：按钮 → 待确认卡（点「开始」才真的开始）→ 开始后可以打开驾驶舱。信息页、编成页、账号页共用。
import { useState } from "react";
import type { Card } from "../lib/chat";
import { useHiro } from "../lib/hiro";
import { cardsFor } from "../lib/quick";
import { ConfirmCard, ErrorCard } from "./Cards";
import { Button } from "./ui";

export function StartTask({ label, args, primary, disabled, onOpenJob }: { label: string; args: string[]; primary?: boolean; disabled?: boolean; onOpenJob?: (jobId: string) => void }) {
  const call = useHiro();
  const [cards, setCards] = useState<Card[]>([]);
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    try {
      setCards(cardsFor(await call(["run", "start", ...args])));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-2">
      <div>
        <Button primary={primary} disabled={busy || disabled} onClick={start}>
          {label}
        </Button>
      </div>
      {cards.map((c, i) =>
        c.type === "confirm" ? (
          <ConfirmCard
            key={i}
            card={c}
            onResolved={(r, a) =>
              setCards((cs) => [
                ...cs.map((x, j) => (j === i && x.type === "confirm" ? { ...x, status: a === "cancel" ? ("cancelled" as const) : r.ok ? ("confirmed" as const) : ("failed" as const) } : x)),
                ...(a === "confirm" ? cardsFor(r) : []),
              ])
            }
          />
        ) : c.type === "error" ? (
          <ErrorCard key={i} card={c} />
        ) : c.type === "run" ? (
          <div key={i} className="text-xs text-ok">
            已开始。
            {onOpenJob && (
              <button type="button" className="font-bold text-acc" onClick={() => onOpenJob(c.jobId)}>
                打开驾驶舱 ›
              </button>
            )}
          </div>
        ) : null,
      )}
    </div>
  );
}
