// Sélecteur de quête — affiché en haut de la page de partie en phase "opening".
// Permet au MJ de choisir un scénario pré-rédigé avant de lancer l'aventure.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/rest";
import type { Scenario } from "../api/types";
import { useParty } from "../store";

interface ScenarioPickerProps {
  partieId: string;
  onSelected?: () => void;
}

export function ScenarioPicker({ partieId, onSelected }: ScenarioPickerProps) {
  const queryClient = useQueryClient();
  const setThinking = useParty((s) => s.setThinking);

  const { data: scenarios, isLoading } = useQuery({
    queryKey: ["scenarios"],
    queryFn: () => api.listScenarios(),
    staleTime: 60_000,
  });

  const setQuest = useMutation({
    mutationFn: (s: Scenario) =>
      api.setQuest(partieId, { titre: s.titre, pitch: s.pitch, source: s.source }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["party", partieId] });
      onSelected?.();
    },
  });

  const handleChoose = (s: Scenario) => {
    setQuest.mutate(s);
  };

  const handleFreeText = () => {
    // Passe à la création de personnage libre sans scénario
    setQuest.mutate({
      id: "libre",
      titre: "",
      pitch: "",
      source: "",
      niveau: "",
      theme: "",
    });
  };

  if (isLoading) {
    return (
      <div className="text-center text-stone-400 text-sm py-6">
        Chargement des scénarios…
      </div>
    );
  }

  const items = scenarios ?? [];

  return (
    <div className="bg-stone-900/80 border border-stone-700 rounded-lg p-4 mb-4">
      <h3 className="text-amber-200 font-serif text-base mb-3">
        📜 Choisissez un scénario pour commencer l'aventure
      </h3>
      <p className="text-stone-400 text-xs mb-4">
        Sélectionnez un scénario pré-rédigé ou créez votre propre aventure.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-3">
        {items.map((s) => (
          <button
            key={s.id}
            onClick={() => handleChoose(s)}
            disabled={setQuest.isPending}
            className="text-left p-3 rounded-lg border border-stone-700 bg-stone-800/50 hover:bg-stone-800 hover:border-amber-600 transition-colors disabled:opacity-50"
          >
            <div className="flex items-baseline gap-2 mb-1">
              <span className="text-amber-400 font-mono text-[10px]">[{s.id}]</span>
              <span className="text-stone-100 text-sm font-medium">{s.titre}</span>
              {s.fichier && (
                <span className="text-emerald-400 text-[10px]" title="PDF complet disponible">📄</span>
              )}
            </div>
            <div className="text-stone-500 text-[10px] mb-1">
              Niveaux {s.niveau} — {s.theme}
            </div>
            <div className="text-stone-300 text-xs line-clamp-2">{s.pitch}</div>
            {s.fichier && (
              <a
                href={s.fichier}
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
