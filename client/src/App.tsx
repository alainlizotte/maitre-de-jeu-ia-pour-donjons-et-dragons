// Coquille — layout + Outlet (reaction-router). Bandeau de connexion état serveur.

import { Outlet, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api/rest";

export default function App() {
  const { data } = useQuery({ queryKey: ["health"], queryFn: api.health });
  const ollamaOk = !!data?.ollama && !!data?.model_available;
  return (
    <div className="h-full flex flex-col">
      <header className="border-b border-stone-800 bg-stone-950/80 px-4 py-2 flex items-center gap-3">
        <Link to="/" className="font-serif text-lg text-amber-300 font-bold">
          🎲 D&D 3.5 — Maître du Jeu
        </Link>
        <span className="ml-auto text-xs text-stone-400 flex items-center gap-3">
          <span className={ollamaOk ? "text-emerald-400" : "text-rose-400"}>
            ● Ollama {ollamaOk ? "ok" : "down"}
          </span>
          {data && (
            <span title={data.model}>
              modèle: <span className="text-stone-200">{data.model}</span>
            </span>
          )}
          {data?.rag?.enabled && (
            <span className="text-amber-300" title="Knowledge Base active">
              📚 RAG ({Object.values(data.rag?.collections ?? {}).reduce((a, b) => a + b, 0)})
            </span>
          )}
        </span>
      </header>
      <main className="flex-1 min-h-0">
        <Outlet />
      </main>
    </div>
  );
}
