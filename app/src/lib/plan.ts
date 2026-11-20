// 开局编成方案的摘要（hiro plan formation / show 的返回）。
export interface PlanSupport {
  id?: string | null;
  name: string | null;
  attr: string | null;
  level: number | null;
  value?: number | null;
  why: string[];
}

export interface PlanMemory {
  id?: string | null;
  card_id?: string | null;
  name: string | null;
  upgrade: number;
  timing: string | null;
  slot?: string | null;
  role?: string | null;
}

export interface PlanSummary {
  created?: string | null;
  idol: { id?: string | null; name?: string | null; plan?: string | null; profile?: string | null };
  main_attr?: string | null;
  support: {
    set?: number | null;
    error?: string | null;
    value?: number | null;
    counts?: Record<string, number> | null;
    own: PlanSupport[];
    rental?: { name?: string | null; attr?: string | null; value?: number | null; estimated?: boolean } | null;
  };
  memory: { set?: number | null; error?: string | null; cards?: string[]; chosen: PlanMemory[] };
  notes: string[];
  path?: string;
  report?: string;
}
