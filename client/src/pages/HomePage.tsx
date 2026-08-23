// Tableau de bord (accueil connecté) — section « Mes personnages » + parties.
// Le joueur choisit ici le personnage (menu déroulant) qu'il incarnera en
// rejoignant ou créant une partie ; il est transmis au WS via le store.

import { useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getToken, setToken } from "../api/rest";
import type { FichePerso } from "../api/types";
import { useParty } from "../store";
import { slugify } from "../utils/slug";

// --------------------------------------------------------------------------- //
//  Portrait avec repli monogramme (pas de retry ComfyUI ici : fiche fraîche).
// --------------------------------------------------------------------------- //
function MiniPortrait({ perso }: { perso: FichePerso }) {
  const [erreur, setErreur] = useState(false);
  if (!perso.portrait || erreur) {
    const initiales =
      perso.nom
        .split(/\s+/)
        .map((w) => w.charAt(0).toUpperCase())
        .slice(0, 2)
        .join("") || "?";
    return (
      <div className="h-28 w-full rounded border border-stone-700 bg-gradient-to-b from-stone-800 to-stone-900 flex items-center justify-center font-serif text-3xl text-amber-500/70">
        {initiales}
      </div>
    );
  }
  return (
    <img
      src={perso.portrait}
      alt={perso.nom}
      className="h-28 w-full object-cover object-top rounded border border-stone-700"
      onError={() => setErreur(true)}
    />
  );
}

function CartePerso({ perso }: { perso: FichePerso }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [confirmer, setConfirmer] = useState(false);
  const slug = slugify(perso.nom);
  const supprimer = useMutation({
    mutationFn: () => api.deletePerso(slug),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["persos"] }),
  });

  return (
    <div className="bg-stone-800/40 border border-stone-700/60 rounded-lg overflow-hidden flex flex-col">
      <MiniPortrait perso={perso} />
      <div className="p-3 flex-1 flex flex-col">
        <div className="text-stone-100 font-medium truncate" title={perso.nom}>
          {perso.nom}
        </div>
        <div className="text-xs text-stone-400">
          {[perso.race, perso.classe, `niv. ${perso.niveau}`].filter(Boolean).join(" · ")}
        </div>
        <div className="text-xs text-stone-500 tabular-nums mt-0.5">
          PV {perso.pv}/{perso.pv_max} · CA {perso.ca} · BBA{" "}
          {perso.bab > 0 ? `+${perso.bab}` : perso.bab}
        </div>
        {!perso.portrait && (
          <div className="text-[10px] text-amber-500/70 mt-1 italic">
            Portrait en génération…
          </div>
        )}
        <div className="mt-auto pt-3 flex gap-2">
          <button
            className="flex-1 px-2 py-1.5 bg-stone-700 hover:bg-stone-600 rounded text-sm"
            onClick={() => navigate(`/personnage/${encodeURIComponent(slug)}`)}
          >
            Modifier
          </button>
          {confirmer ? (
            <>
              <button
                className="px-2 py-1.5 bg-rose-700 hover:bg-rose-600 rounded text-sm"
                onClick={() => supprimer.mutate()}
                disabled={supprimer.isPending}
              >
                {supprimer.isPending ? "…" : "Confirmer"}
              </button>
              <button
                className="px-2 py-1.5 bg-stone-700 hover:bg-stone-600 rounded text-sm"
                onClick={() => setConfirmer(false)}
              >
                ✕
              </button>
            </>
          ) : (
            <button
              className="px-2 py-1.5 bg-stone-700 hover:bg-rose-900/60 rounded text-sm"
              onClick={() => setConfirmer(true)}
              title="Supprimer ce personnage"
            >
              🗑️
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function HomePage() {
  const utilisateur = useParty((s) => s.utilisateur);
  const setUtilisateur = useParty((s) => s.setUtilisateur);
  const setPartieId = useParty((s) => s.setPartieId);
  const setPassword = useParty((s) => s.setPassword);
  const personnage = useParty((s) => s.personnage);
  const setPersonnage = useParty((s) => s.setPersonnage);
  const reset = useParty((s) => s.reset);
  const [titre, setTitre] = useState("");
  const [mdpCreation, setMdpCreation] = useState("");
  const navigate = useNavigate();
  const qc = useQueryClient();

  // Partie protégée sélectionnée : affiche le formulaire de mot de passe.
  const [pendingJoin, setPendingJoin] = useState<{ id: string; titre: string } | null>(null);
  const [mdpJoin, setMdpJoin] = useState("");
  const [mdpErreur, setMdpErreur] = useState("");

  // Non connecté → page de connexion.
  if (!getToken()) {
    return <Navigate to="/connexion" replace />;
  }

  const persos = useQuery({
    queryKey: ["persos"],
    queryFn: api.listPersos,
    enabled: !!getToken(),
  });
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

  const deconnexion = () => {
    setToken("");
    setUtilisateur("");
    navigate("/connexion");
  };

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

  const mesPersos = persos.data ?? [];
  const labelPersoChoisi =
    mesPersos.find((p) => p.nom === personnage)?.nom ?? "";

  const rejoindre = (id: string, mdp?: string) => {
    reset();
    setPartieId(id);
    setPassword(mdp ?? "");
    navigate(`/partie/${id}`);
  };

  return (
    <div className="max-w-4xl w-full mx-auto p-6 overflow-y-auto">
      <div className="mb-6 flex items-start gap-3">
        <div>
          <h1 className="font-serif text-3xl text-amber-300 mb-1">
            Bienvenue, {utilisateur || "aventurier"}
          </h1>
          <p className="text-stone-400 text-sm">
            Gérez vos personnages puis lancez ou rejoignez une partie.
          </p>
        </div>
        <button
          onClick={deconnexion}
          className="ml-auto px-3 py-1.5 bg-stone-800 hover:bg-stone-700 border border-stone-700 rounded text-sm text-stone-300 shrink-0"
        >
          Déconnexion
        </button>
      </div>

      {/* ------------------------- MES PERSONNAGES ------------------------- */}
      <section className="mb-8">
        <div className="flex items-center gap-3 mb-3">
          <h2 className="font-serif text-xl text-amber-200">Mes personnages</h2>
          <Link
            to="/personnage/nouveau"
            className="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 rounded font-medium text-stone-900 text-sm"
          >
            + Créer un personnage
          </Link>
        </div>

        {persos.isLoading && <p className="text-stone-400 text-sm">Chargement…</p>}
        {persos.isError && (
          <p className="text-rose-400 text-sm">⚠️ {(persos.error as Error).message}</p>
        )}
        {!persos.isLoading && mesPersos.length === 0 && (
          <p className="text-stone-400 text-sm italic">
            Aucun personnage pour l'instant. Créez votre premier héros avec le
            formulaire — calculs et portrait automatiques.
          </p>
        )}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {mesPersos.map((p) => (
            <CartePerso key={p.nom} perso={p} />
          ))}
        </div>
      </section>

      {/* ------------------------------ PARTIES ---------------------------- */}
      <section>
        <h2 className="font-serif text-xl text-amber-200 mb-3">Parties</h2>

        {/* Sélecteur du personnage à incarner (menu déroulant). */}
        <div className="bg-stone-800/50 rounded-lg p-4 mb-4">
          <label className="block">
            <span className="text-stone-300 text-sm">
              Personnage incarné dans la partie
            </span>
            <select
              className="mt-1 w-full bg-stone-900 border border-stone-700 rounded px-3 py-2 focus:outline-none focus:border-amber-400"
              value={personnage}
              onChange={(e) => setPersonnage(e.target.value)}
            >
              <option value="">— Choisir un personnage —</option>
              {mesPersos.map((p) => (
                <option key={p.nom} value={p.nom}>
                  {p.nom} ({[p.race, p.classe].filter(Boolean).join(" · ")})
                </option>
              ))}
            </select>
          </label>
          {personnage && (
            <p className="text-xs text-emerald-400 mt-1">
              ✓ Vous rejoindrez les parties en tant que{" "}
              <strong>{labelPersoChoisi}</strong>.
            </p>
          )}
        </div>

        <div className="bg-stone-800/50 rounded-lg p-4 mb-6">
          <h3 className="font-serif text-lg text-amber-200 mb-2">Nouvelle partie</h3>
          <div className="flex gap-2 mb-2">
            <input
              className="flex-1 bg-stone-900 border border-stone-700 rounded px-3 py-2"
              value={titre}
              onChange={(e) => setTitre(e.target.value)}
              placeholder="Titre de la campagne (optionnel)"
            />
            <button
              className="px-4 py-2 bg-amber-600 hover:bg-amber-500 disabled:opacity-40 rounded font-medium text-stone-900"
              disabled={!utilisateur || create.isPending}
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
          <h3 className="font-serif text-lg text-amber-200 mb-2">Parties existantes</h3>
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
      </section>
    </div>
  );
}
