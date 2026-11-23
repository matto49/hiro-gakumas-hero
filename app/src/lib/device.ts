// 侧栏的游戏连接状态：启动时、任务结束后用 `hiro doctor --probe` 试连一次（只读截一张图），点状态行再试。
// 有任务在跑时不试连（不和任务抢游戏），显示任务进行中。
import { useCallback, useEffect, useRef, useState } from "react";
import { data, type Caller } from "./hiro";

export interface DeviceStatus {
  tone: "ok" | "off" | "err" | "acc";
  text: string;
  title: string;
}

interface DoctorItem {
  id: string;
  ok: boolean | null;
  detail: string;
  next?: string;
}

export const DEVICE_UNKNOWN: DeviceStatus = { tone: "off", text: "还没试连游戏 · 点这里试连", title: "" };
const DEVICE_PROBING: DeviceStatus = { tone: "off", text: "正在试连游戏…", title: "" };

export function deviceFromDoctor(items: DoctorItem[]): DeviceStatus {
  const p = items.find((i) => i.id === "platform");
  if (p?.ok) {
    const label = p.detail.split("：")[0];
    return { tone: "ok", text: `${label} 已连上`, title: p.detail };
  }
  const title = p ? [p.detail, p.next].filter(Boolean).join("\n") : "没拿到游戏平台的检查结果";
  return { tone: "err", text: "游戏未连接 · 点这里重试", title };
}

export function useDevice(call: Caller, jobRunning: boolean): [DeviceStatus, () => void] {
  const [st, setSt] = useState<DeviceStatus>(DEVICE_UNKNOWN);
  const busy = useRef(false);
  const probe = useCallback(() => {
    if (busy.current) return;
    busy.current = true;
    setSt(DEVICE_PROBING);
    data<{ items: DoctorItem[] }>(call, ["doctor", "--probe"])
      .then(
        (r) => setSt(deviceFromDoctor(r.items)),
        (e) => setSt({ tone: "err", text: "游戏未连接 · 点这里重试", title: String(e?.message ?? e) }),
      )
      .finally(() => {
        busy.current = false;
      });
  }, [call]);
  // 启动时试一次；任务结束（jobRunning 由真变假）后再试一次
  useEffect(() => {
    if (!jobRunning) probe();
  }, [jobRunning, probe]);
  if (jobRunning) return [{ tone: "acc", text: "任务进行中", title: "任务结束后会再试连一次游戏" }, probe];
  return [st, probe];
}
