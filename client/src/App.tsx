// Coquille — layout + Outlet (react-router). Bandeau : état serveur + compte
// connecté (déconnexion). Les gardes de routes vivent dans chaque page.

import { Outlet, Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, getToken, setToken } from "./api/rest";
import { useParty } from "./store";

export default function App() {
  const { data } = useQuery({ queryKey: ["health"], queryFn: api.health });
  const utilisateur = useParty((s) => s.utilisateur);
  const setUtilisateur = useParty((s) => s.setUtilisateur);
  const navigate = useNavigate();
  const backendOk = !!data?.ok && !!data?.model_available;
  const backendLabel = data?.backend === "llamacpp" ? "llama.cpp" : "Ollama";
  const connecte = Boolean(getToken());

  const deconnexion = () => {
    setToken("");
    setUtilisateur("");
    navigate("/connexion");
  };

  return (
    <div className="h-full flex flex-col">
      <header className="border-b border-stone-800 bg-stone-950/80 px-4 py-2 flex items-center gap-3">
        <Link to="/" className="font-serif text-lg text-amber-300 font-bold">
          🎲 D&D 3.5 — Maître du Jeu
        </Link>
        <span className="text-xs text-stone-400 flex items-center gap-3">
          <span className={backendOk ? "text-emerald-400" : "text-rose-400"}>
            ● {backendLabel} {backendOk ? "ok" : "down"}
          </span>
          {data?.model && (
            <span className="text-stone-500 max-w-48 truncate" title={data.model}>
              {data.model}
            </span>
          )}
          {data?.rag?.enabled && (
            <span className="text-amber-300" title="Knowledge Base active">
              📚 RAG ({Object.values(data.rag?.collections ?? {}).reduce((a, b) => a + b, 0)})
            </span>
          )}
        </span>
        <span className="ml-auto flex items-center gap-2 text-sm">
          {connecte && (
            <>
              <span className="text-stone-300">👤 {utilisateur || "connecté"}</span>
              <button
                onClick={deconnexion}
                className="px-2.5 py-1 bg-stone-800 hover:bg-stone-700 border border-stone-700 rounded text-xs text-stone-300"
              >
                Déconnexion
              </button>
            </>
          )}
        </span>
      </header>
      <main className="flex-1 min-h-0 flex flex-col overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
