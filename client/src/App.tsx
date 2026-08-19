// Coquille — layout + Outlet (reaction-router). Bandeau de connexion état serveur
// + sélecteur de modèle IA (liste Ollama ou saisie libre).

import { useState } from "react";
import { Outlet, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api/rest";

const FREE_ENTRY = "__custom__";

function ModelSelector() {
  const qc = useQueryClient();
  const [customMode, setCustomMode] = useState(false);
  const [customModel, setCustomModel] = useState("");
  const [erreur, setErreur] = useState("");

  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const models = useQuery({ queryKey: ["models"], queryFn: api.listModels });

  const switchModel = useMutation({
    mutationFn: (model: string) => api.setModel(model),
    onSuccess: () => {
      setErreur("");
      setCustomMode(false);
      qc.invalidateQueries({ queryKey: ["health"] });
      qc.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (e: Error) => setErreur(e.message),
  });

  const current = health.data?.model ?? models.data?.current ?? "";
  const list = models.data?.models ?? [];
  const currentKnown = list.includes(current);

  // Mode saisie libre : champ texte pour un modèle absent de la liste déroulante.
  if (customMode) {
    return (
      <span className="flex items-center gap-1" title="Saisie libre du modèle">
        <input
          autoFocus
          className="w-44 bg-stone-800 border border-stone-700 rounded px-2 py-0.5 text-xs focus:outline-none focus:border-amber-400"
          value={customModel}
          placeholder="nom_du_modele:tag"
          onChange={(e) => setCustomModel(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && customModel.trim()) switchModel.mutate(customModel.trim());
            if (e.key === "Escape") setCustomMode(false);
          }}
        />
        <button
          className="text-amber-300 hover:text-amber-200 disabled:opacity-40"
          disabled={!customModel.trim() || switchModel.isPending}
          onClick={() => switchModel.mutate(customModel.trim())}
        >
          ✓
        </button>
        <button
          className="text-stone-400 hover:text-stone-200"
          onClick={() => {
            setCustomMode(false);
            setErreur("");
          }}
        >
          ✕
        </button>
        {erreur && <span className="text-rose-400 max-w-56 truncate" title={erreur}>⚠️</span>}
      </span>
    );
  }

  return (
    <span className="flex items-center gap-1">
      <select
        className="bg-stone-800 border border-stone-700 rounded px-1.5 py-0.5 text-xs max-w-52 focus:outline-none focus:border-amber-400"
        value={currentKnown ? current : FREE_ENTRY}
        onChange={(e) => {
          if (e.target.value === FREE_ENTRY) {
            setCustomModel(current && !currentKnown ? current : "");
            setCustomMode(true);
          } else {
            switchModel.mutate(e.target.value);
          }
        }}
        title={current}
      >
        {!currentKnown && current && <option value={FREE_ENTRY}>{current} (hors liste)</option>}
        {list.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
        <option value={FREE_ENTRY}>✏️ Autre (saisie libre)…</option>
      </select>
      {switchModel.isPending && <span className="text-stone-500">…</span>}
      {erreur && <span className="text-rose-400 max-w-56 truncate" title={erreur}>⚠️</span>}
    </span>
  );
}

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
          <span className="flex items-center gap-1">
            modèle: <ModelSelector />
          </span>
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
