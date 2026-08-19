import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/rest";
import { useParty } from "../store";

export function HomePage() {
  const player = useParty((s) => s.player);
  const setPlayer = useParty((s) => s.setPlayer);
  const setPartieId = useParty((s) => s.setPartieId);
  const setPassword = useParty((s) => s.setPassword);
  const reset = useParty((s) => s.reset);
  const [titre, setTitre] = useState("");
  const [mdpCreation, setMdpCreation] = useState("");
  const navigate = useNavigate();
  const qc = useQueryClient();

  // Partie protégée sélectionnée : affiche le formulaire de mot de passe.
  const [pendingJoin, setPendingJoin] = useState<{ id: string; titre: string } | null>(null);
  const [mdpJoin, setMdpJoin] = useState("");
  const [mdpErreur, setMdpErreur] = useState("");

  const parties = useQuery({
    queryKey: ["parties"],
    queryFn: api.listParties,
  });
  const create = useMutation({
    mutationFn: () => api.createParty(titre.trim() || "Nouvelle partie", mdpCreation.trim()),
    onSuccess: (d) => {
      reset();
      setPartieId(d.partie_id);
      setPassword(mdpCreation.trim());
      qc.invalidateQueries({ queryKey: ["parties"] });
      navigate(`/partie/${d.partie_id}`);
    },
  });

  const data = parties.data;
  const details = data?.details ?? {};
  const seen = new Set<string>();
  const list: { id: string; active: boolean; titre: string; phase: string; tour: number; pj: number; protegee: boolean }[] = [];
  for (const id of [...(data?.active ?? []), ...(data?.persisted ?? [])]) {
    if (seen.has(id)) continue;
    seen.add(id);
    const d = details[id];
    list.push({
      id,
      active: (data?.active ?? []).includes(id),
      titre: d?.titre ?? "(sans titre)",
      phase: d?.phase ?? "opening",
      tour: d?.tour ?? 0,
      pj: d?.pj ?? 0,
      protegee: !!d?.protegee,
    });
  }

  const rejoindre = (id: string, mdp?: string) => {
    reset();
    setPartieId(id);
    setPassword(mdp ?? "");
    navigate(`/partie/${id}`);
  };

  return (
    <div className="max-w-3xl mx-auto p-6">
      <div className="mb-6">
        <h1 className="font-serif text-3xl text-amber-300 mb-1">
          D&D 3.5 — Maître du Jeu
        </h1>
        <p className="text-stone-400 text-sm">
          Entrez votre pseudo, puis créez une nouvelle partie ou reprenez une existante.
        </p>
      </div>

      <label className="block mb-4">
        <span className="text-stone-300 text-sm">Votre pseudo</span>
        <input
          className="mt-1 w-full bg-stone-800 border border-stone-700 rounded px-3 py-2 focus:outline-none focus:border-amber-400"
          value={player}
          onChange={(e) => setPlayer(e.target.value)}
          placeholder="joueur 1"
          maxLength={24}
        />
      </label>

      <div className="bg-stone-800/50 rounded-lg p-4 mb-6">
        <h2 className="font-serif text-lg text-amber-200 mb-2">Nouvelle partie</h2>
        <div className="flex gap-2 mb-2">
          <input
            className="flex-1 bg-stone-900 border border-stone-700 rounded px-3 py-2"
            value={titre}
            onChange={(e) => setTitre(e.target.value)}
            placeholder="Titre de la campagne (optionnel)"
          />
          <button
            className="px-4 py-2 bg-amber-600 hover:bg-amber-500 disabled:opacity-40 rounded font-medium text-stone-900"
            disabled={!player.trim() || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending ? "Création…" : "Créer"}
          </button>
        </div>
        <div className="flex gap-2">
          <input
            type="password"
            className="flex-1 bg-stone-900 border border-stone-700 rounded px-3 py-2 text-sm"
            value={mdpCreation}
            onChange={(e) => setMdpCreation(e.target.value)}
            placeholder="Mot de passe (optionnel — imposé aux joueurs pour rejoindre)"
            maxLength={64}
          />
        </div>
        {create.isError && (
          <p className="text-rose-400 text-xs mt-2">⚠️ {(create.error as Error).message}</p>
        )}
      </div>

      <div>
        <h2 className="font-serif text-lg text-amber-200 mb-2">Parties existantes</h2>
        {parties.isLoading && <p className="text-stone-400">Chargement…</p>}
        {!parties.isLoading && list.length === 0 && (
          <p className="text-stone-400 text-sm">Aucune partie. Créez-en une ci-dessus.</p>
        )}
        <ul className="space-y-2">
          {list.map((p) => (
            <li key={p.id} className="bg-stone-800/30 rounded p-3">
              <div className="flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="text-stone-100 font-medium truncate">
                    {p.protegee && <span title="Partie protégée">🔒 </span>}
                    {p.titre}
                  </div>
                  <div className="text-xs text-stone-400 flex items-center gap-2">
                    <code className="text-amber-300">{p.id}</code>
                    <span>·</span>
                    <span>{p.phase}</span>
                    <span>·</span>
                    <span>tour {p.tour}</span>
                    {p.pj > 0 && (
                      <>
                        <span>·</span>
                        <span>{p.pj} PJ</span>
                      </>
                    )}
                  </div>
                  <div className="text-xs text-stone-500 mt-0.5">
                    {p.active ? "● active en mémoire" : "◆ persistée sur disque"}
                  </div>
                </div>
                {p.protegee ? (
                  <button
                    className="px-3 py-1.5 bg-stone-700 hover:bg-stone-600 rounded text-sm shrink-0"
                    onClick={() => {
                      setPendingJoin(pendingJoin?.id === p.id ? null : { id: p.id, titre: p.titre });
                      setMdpJoin("");
                      setMdpErreur("");
                    }}
                  >
                    {pendingJoin?.id === p.id ? "Annuler" : "🔒 Rejoindre"}
                  </button>
                ) : (
                  <button
                    className="px-3 py-1.5 bg-stone-700 hover:bg-stone-600 rounded text-sm shrink-0"
                    onClick={() => rejoindre(p.id)}
                  >
                    Rejoindre
                  </button>
                )}
              </div>
              {pendingJoin?.id === p.id && (
                <form
                  className="mt-3 flex gap-2 items-center"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (!mdpJoin.trim()) {
                      setMdpErreur("Saisissez le mot de passe de la partie.");
                      return;
                    }
                    rejoindre(p.id, mdpJoin.trim());
                  }}
                >
                  <input
                    autoFocus
                    type="password"
                    className="flex-1 bg-stone-900 border border-stone-700 rounded px-3 py-1.5 text-sm focus:outline-none focus:border-amber-400"
                    value={mdpJoin}
                    onChange={(e) => setMdpJoin(e.target.value)}
                    placeholder={`Mot de passe pour « ${p.titre} »`}
                    maxLength={64}
                  />
                  <button
                    type="submit"
                    className="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 rounded text-sm font-medium text-stone-900"
                  >
                    Entrer
                  </button>
                  {mdpErreur && <span className="text-rose-400 text-xs">{mdpErreur}</span>}
                </form>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
