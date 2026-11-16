import { useEffect, useState } from "react";
import { hiro, type Envelope } from "./lib/hiro";

export function App() {
  const [summary, setSummary] = useState<Envelope | null>(null);
  useEffect(() => {
    hiro(["info", "summary"]).then(setSummary);
  }, []);
  return (
    <main className="p-6">
      <h1 className="text-xl font-bold">数字小广</h1>
      <pre className="mt-4 text-xs">{summary ? JSON.stringify(summary, null, 2) : "读取中…"}</pre>
    </main>
  );
}
