// Sélecteur de quête — affiché en haut de la page de partie en phase "opening".
// Permet au MJ de choisir un univers, puis un scénario pré-rédigé.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/rest";
import type { Scenario, Universe } from "../api/types";
import { useParty } from "../store";

interface ScenarioPickerProps {
  partieId: string;
  onSelected?: () => void;
}

export function ScenarioPicker({ partieId, onSelected }: ScenarioPickerProps) {
  const queryClient = useQueryClient();
  const setThinking = useParty((s) => s.setThinking);
  const [selectedUniverse, setSelectedUniverse] = useState<Universe | null>(null);

  const { data: universes, isLoading } = useQuery({
    queryKey: ["scenarios"],
    queryFn: () => api.listScenarios(),
    staleTime: 60_000,
  });

  const setQuest = useMutation({
    mutationFn: (s: Scenario) =>
      api.setQuest(partieId, {
        titre: s.titre,
        pitch: s.pitch,
        source: `[${s.id}] ${s.pdf ?? selectedUniverse?.nom ?? ""}`,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["party", partieId] });
      onSelected?.();
    },
  });

  const handleChoose = (s: Scenario) => {
    setQuest.mutate(s);
  };

  const handleFreeText = () => {
    setQuest.mutate({
      id: "libre",
      titre: "",
      pitch: "",
      pdf: null,
    });
  };

  if (isLoading) {
    return (
      <div className="text-center text-stone-400 text-sm py-6">
        Chargement des scénarios…
      </div>
    );
  }

  const items = universes ?? [];

  // ── Écran 2 : scénarios de l'univers sélectionné ── //
  if (selectedUniverse) {
    return (
      <div className="bg-stone-900/80 border border-stone-700 rounded-lg p-4 mb-4">
        <div className="flex items-center gap-2 mb-3">
          <button
            onClick={() => setSelectedUniverse(null)}
            className="text-stone-400 hover:text-stone-200 text-sm"
          >
            ← Retour
          </button>
          <h3 className="text-amber-200 font-serif text-base">
            {selectedUniverse.nom}
          </h3>
        </div>
        {selectedUniverse.description && (
          <p className="text-stone-400 text-xs mb-3 italic">
            {selectedUniverse.description}
          </p>
        )}
        {selectedUniverse.cartes && selectedUniverse.cartes.length > 0 && (
          <div className="flex gap-2 mb-3 flex-wrap">
            {selectedUniverse.cartes.map((c) => (
              <a
                key={c.fichier}
                href={c.fichier}
                target="_blank"
                rel="noreferrer"
                className="text-sky-400 hover:text-sky-300 text-[10px] underline"
              >
                🗺️ {c.nom}
              </a>
            ))}
          </div>
        )}
        <div className="max-h-[55vh] overflow-y-auto pr-1 mb-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {selectedUniverse.scenarios.map((s) => (
              <button
                key={s.id}
                onClick={() => handleChoose(s)}
                disabled={setQuest.isPending}
                className="text-left p-3 rounded-lg border border-stone-700 bg-stone-800/50 hover:bg-stone-800 hover:border-amber-600 transition-colors disabled:opacity-50"
              >
                <div className="flex items-baseline gap-2 mb-1">
                  <span className="text-stone-100 text-sm font-medium">{s.titre}</span>
                  {s.pdf && (
                    <span className="text-emerald-400 text-[10px]" title="PDF complet disponible">PDF</span>
                  )}
                </div>
                {(s.niveau || s.joueurs) && (
                  <div className="flex gap-2 text-[10px] mb-1">
                    {s.niveau && (
                      <span className="text-amber-400/80">⭐ Niv. {s.niveau}</span>
                    )}
                    {s.joueurs && (
                      <span className="text-sky-400/80">👤 {s.joueurs} joueurs</span>
                    )}
                  </div>
                )}
                {s.pitch && (
                  <div className="text-stone-300 text-xs line-clamp-2">{s.pitch}</div>
                )}
                {/* Assets indicators */}
                <div className="flex gap-2 mt-1 flex-wrap">
                  {s.cartes && s.cartes.length > 0 && (
                    <span className="text-sky-400 text-[10px]">🗺️ {s.cartes.length} cartes</span>
                  )}
                  {s.artwork && (
                    <span className="text-purple-400 text-[10px]">
                      🎨 {(s.artwork.lieux?.length ?? 0) + (s.artwork.monstres?.length ?? 0) + (s.artwork.pnj?.length ?? 0)} artworks
                    </span>
                  )}
                  {s.objets && s.objets.length > 0 && (
                    <span className="text-amber-400 text-[10px]">⚔️ {s.objets.length} objets</span>
                  )}
                  {s.enigmes && s.enigmes.length > 0 && (
                    <span className="text-rose-400 text-[10px]">🧩 {s.enigmes.length} énigmes</span>
                  )}
                  {s.annexes && s.annexes.length > 0 && (
                    <span className="text-stone-400 text-[10px]">📎 {s.annexes.length} annexes</span>
                  )}
                </div>
                {s.pdf && (
                  <a
                    href={s.pdf}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="text-emerald-400 hover:text-emerald-300 text-[10px] mt-1 inline-block underline"
                  >
                    Consulter le PDF
                  </a>
                )}
              </button>
            ))}
          </div>
        </div>
        <div className="flex justify-between items-center pt-2 border-t border-stone-700">
          <button
            onClick={handleFreeText}
            disabled={setQuest.isPending}
            className="text-stone-400 hover:text-stone-200 text-xs underline underline-offset-2 disabled:opacity-50"
          >
            Aventure libre (pas de scénario)
          </button>
          {setQuest.isPending && (
            <span className="text-stone-500 text-xs animate-pulse">Enregistrement…</span>
          )}
        </div>
      </div>
    );
  }

  // ── Écran 1 : sélection de l'univers ── //
  return (
    <div className="bg-stone-900/80 border border-stone-700 rounded-lg p-4 mb-4">
      <h3 className="text-amber-200 font-serif text-base mb-3">
        📜 Choisissez un univers pour commencer l'aventure
      </h3>
      <p className="text-stone-400 text-xs mb-4">
        Sélectionnez un univers, puis un scénario pré-rédigé, ou créez votre propre aventure.
      </p>
      <div className="max-h-[60vh] overflow-y-auto pr-1 mb-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {items.map((u) => (
            <button
              key={u.id}
              onClick={() => setSelectedUniverse(u)}
              className="text-left p-4 rounded-lg border border-stone-700 bg-stone-800/50 hover:bg-stone-800 hover:border-amber-600 transition-colors"
            >
              <div className="text-stone-100 text-base font-serif font-medium mb-1">
                {u.nom}
              </div>
              {u.description && (
                <div className="text-stone-400 text-xs line-clamp-2 mb-2">
                  {u.description}
                </div>
              )}
              <div className="text-amber-400/80 text-[11px]">
                {u.scenarios.length} scénario{u.scenarios.length > 1 ? "s" : ""}
                {u.cartes && u.cartes.length > 0 && (
                  <span className="text-sky-400/80 ml-2">· {u.cartes.length} carte{u.cartes.length > 1 ? "s" : ""}</span>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>
      <div className="flex justify-between items-center pt-2 border-t border-stone-700">
        <button
          onClick={handleFreeText}
          disabled={setQuest.isPending}
          className="text-stone-400 hover:text-stone-200 text-xs underline underline-offset-2 disabled:opacity-50"
        >
          Aventure libre (pas de scénario)
        </button>
        {setQuest.isPending && (
          <span className="text-stone-500 text-xs animate-pulse">Enregistrement…</span>
        )}
      </div>
    </div>
  );
}
