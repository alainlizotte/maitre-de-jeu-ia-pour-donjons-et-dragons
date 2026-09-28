// Coquille — layout + Outlet (react-router). Bandeau : état serveur + compte
// connecté (déconnexion). Les gardes de routes vivent dans chaque page.

import { Outlet, Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, getToken, setToken } from "./api/rest";
import { useParty } from "./store";

export default function App() {
  // 🔧 Bêta (résidu 3) : polling ADAPTATIF — 30 s quand tout va bien, 5 s
  // dès que le statut est inconnu/perdu (redémarrage serveur, coupure réseau)
  // : le bandeau « down » ne reste plus collé une minute après le retour du
  // backend. `staleTime` évite le refetch immédiat au remount.
  const { data } = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    staleTime: 20_000,
    refetchInterval: (q) => (q.state.data?.ok ? 30_000 : 5_000),
  });
  const utilisateur = useParty((s) => s.utilisateur);
  const setUtilisateur = useParty((s) => s.setUtilisateur);
  const navigate = useNavigate();
  // 🔧 Bêta (résidu 3) : « down » = le backend NE répond PAS. Un modèle
  // déchargé après inactivité (unload_after_turn) n'est PAS un down : il
  // est rechargé à la volée au prochain tour — affiché « en veille ».
  const backendInconnu = data === undefined;
  const backendJoignable = data?.ok === true;
  const backendOk = backendJoignable && !!data?.model_available;
  const backendEnVeille = backendJoignable && !data?.model_available;
  const backendLabel = data?.backend === "llamacpp" ? "llama.cpp" : "Ollama";
  const connecte = Boolean(getToken());

  const deconnexion = () => {
    setToken("");
    setUtilisateur("");
    navigate("/connexion");
  };

  return (
    <div className="h-full flex flex-col">
      <header className="border-b border-stone-800 bg-stone-950/80 px-3 md:px-4 py-2 flex items-center gap-2 md:gap-3">
        <Link to="/" className="font-serif text-lg text-amber-300 font-bold shrink-0">
          <span className="md:hidden">🎲 D&D</span>
          <span className="hidden md:inline">🎲 D&D 3.5 — Maître du Jeu</span>
        </Link>
        <span className="text-xs text-stone-400 flex items-center gap-2 md:gap-3 min-w-0">
          <span className={
            backendInconnu ? "text-stone-500"
              : backendOk ? "text-emerald-400"
                : backendEnVeille ? "text-sky-400"
                  : "text-rose-400"
          }>
            {backendInconnu ? (
              <>● backend…</>
            ) : (
              <>● {backendLabel}{" "}
                {backendOk ? "ok" : backendEnVeille ? "ok · modèle en veille" : "down"}
              </>
            )}
          </span>
          {data?.model && (
            <span className="hidden md:inline text-stone-500 max-w-48 truncate" title={data.model}>
              {data.model}
            </span>
          )}
          {data?.rag?.enabled && (
            <span className="hidden md:inline text-amber-300" title="Knowledge Base active">
              📚 RAG ({Object.values(data.rag?.collections ?? {}).reduce((a, b) => a + b, 0)})
            </span>
          )}
        </span>
        <span className="ml-auto flex items-center gap-2 text-sm">
          {connecte && (
            <>
              <span className="text-stone-300 truncate max-w-24 md:max-w-none">👤 {utilisateur || "connecté"}</span>
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
