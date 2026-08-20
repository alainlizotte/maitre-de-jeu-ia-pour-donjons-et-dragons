// Barre de ressources — liens permanents (manuels, cartes, scénarios PDF)
// affichée en bas de l'écran de jeu, toujours visibles pendant la partie.

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/rest";

/** Libellés courts pour les manuels (les titres complets en tooltip). */
const COURT: Record<string, string> = {
  "Manuel du Joueur 3.5": "Manuel du Joueur",
  "Guide du Maître 3.5": "Guide du Maître",
  "Manuel des Monstres 3.5": "Manuel des Monstres",
  "Errata 3.5": "Errata",
  "FAQ 3.5": "FAQ",
  "Aide — Choix d'un personnage": "Aide perso",
};

export function RessourcesBar({ partie_id }: { partie_id?: string }) {
  const [scenariosOuverts, setScenariosOuverts] = useState(false);
  const { data } = useQuery({
    queryKey: ["ressources", partie_id ?? null],
    queryFn: () => api.ressources(partie_id),
    staleTime: 60_000,
    refetchInterval: 60_000, // le donjon peut apparaître en cours de partie
  });

  if (!data) return null;

  return (
    <footer className="relative shrink-0 border-t border-stone-800 bg-stone-950/80 px-3 py-1.5 flex items-center gap-x-3 gap-y-1 flex-wrap text-xs">
      <span className="text-stone-500 uppercase tracking-wide text-[10px]">Ressources</span>

      {/* Manuels — liens directs permanents */}
      {data.manuels.map((m) => (
        <a
          key={m.url}
          href={m.url}
          target="_blank"
          rel="noreferrer"
          title={m.description}
          className="text-amber-300/90 hover:text-amber-200 underline decoration-amber-700 underline-offset-2"
        >
          📕 {COURT[m.titre] ?? m.titre}
        </a>
      ))}

      {/* Cartes — monde + donjon courant */}
      {data.cartes.map((c) => (
        <a
          key={c.url}
          href={c.url}
          target="_blank"
          rel="noreferrer"
          title={c.titre}
          className="text-sky-300/90 hover:text-sky-200 underline decoration-sky-800 underline-offset-2"
        >
          🗺️ {c.titre.includes("haute") ? "Carte HD" : "Carte"}
        </a>
      ))}
      {data.donjon && (
        <a
          href={data.donjon}
          target="_blank"
          rel="noreferrer"
          title="Carte du donjon (SVG)"
          className="text-sky-300 hover:text-sky-200 font-medium underline decoration-sky-800 underline-offset-2"
        >
          🧭 Donjon
        </a>
      )}

      {/* Scénarios — popover (9 PDF aux titres longs) */}
      <div className="relative">
        <button
          onClick={() => setScenariosOuverts((v) => !v)}
          className="text-emerald-300/90 hover:text-emerald-200 underline decoration-emerald-800 underline-offset-2"
        >
          📜 Scénarios ({data.scenarios.length})
          {scenariosOuverts ? " ▴" : " ▾"}
        </button>
        {scenariosOuverts && (
          <div className="absolute bottom-full mb-2 left-0 z-40 w-80 max-h-72 overflow-auto bg-stone-900 border border-stone-700 rounded-lg shadow-xl p-2">
            <div className="text-stone-500 text-[10px] uppercase mb-1">
              Scénarios PDF — clic pour consulter
            </div>
            {data.scenarios.map((s) => (
              <a
                key={s.id}
                href={s.url}
                target="_blank"
                rel="noreferrer"
                className="block px-2 py-1.5 rounded hover:bg-stone-800 text-stone-200"
                title={`Niveaux : ${s.niveau}`}
              >
                <span className="text-emerald-300 font-mono text-[10px] mr-1.5">
                  [{s.id}]
                </span>
                {s.titre}
                <span className="block text-[10px] text-stone-500">
                  Niveaux : {s.niveau}
                </span>
              </a>
            ))}
          </div>
        )}
      </div>
    </footer>
  );
}
