// Page de connexion — création de compte et connexion (comptes locaux).
// Après connexion : le pseudo joueur = nom du compte, redirection vers l'accueil.

import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api, setToken, getToken } from "../api/rest";
import { useParty } from "../store";

export function LoginPage() {
  const [mode, setMode] = useState<"connexion" | "inscription">("connexion");
  const [nom, setNom] = useState("");
  const [mdp, setMdp] = useState("");
  const [mdp2, setMdp2] = useState("");
  const [erreur, setErreur] = useState("");
  const [enCours, setEnCours] = useState(false);
  const navigate = useNavigate();
  const setUtilisateur = useParty((s) => s.setUtilisateur);
  const setPlayer = useParty((s) => s.setPlayer);

  // Déjà connecté → accueil directement.
  if (getToken()) {
    return <Navigate to="/" replace />;
  }

  const soumettre = async (e: React.FormEvent) => {
    e.preventDefault();
    setErreur("");
    if (!nom.trim() || !mdp) {
      setErreur("Saisissez un nom d'utilisateur et un mot de passe.");
      return;
    }
    if (mode === "inscription" && mdp !== mdp2) {
      setErreur("Les mots de passe ne correspondent pas.");
      return;
    }
    setEnCours(true);
    try {
      const reponse =
        mode === "connexion"
          ? await api.connexion(nom.trim(), mdp)
          : await api.inscription(nom.trim(), mdp);
      setToken(reponse.token);
      // Le pseudo en jeu = compte connecté.
      setUtilisateur(reponse.utilisateur);
      setPlayer(reponse.utilisateur);
      navigate("/", { replace: true });
    } catch (err) {
      setErreur((err as Error).message || "Échec de la connexion.");
    } finally {
      setEnCours(false);
    }
  };

  return (
    <div className="flex-1 flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <h1 className="font-serif text-3xl text-amber-300 text-center mb-1">
          🎲 D&D 3.5 — Maître du Jeu
        </h1>
        <p className="text-stone-400 text-sm text-center mb-6">
          Connectez-vous pour accéder à vos personnages et à vos parties.
        </p>

        <form
          onSubmit={soumettre}
          className="bg-stone-800/50 border border-stone-700 rounded-lg p-5 space-y-4"
        >
          {/* Onglets Connexion / Inscription */}
          <div className="flex rounded overflow-hidden border border-stone-700 text-sm">
            {(["connexion", "inscription"] as const).map((m) => (
              <button
                key={m}
                type="button"
                className={
                  "flex-1 py-2 font-medium capitalize transition-colors " +
                  (mode === m
                    ? "bg-amber-600 text-stone-900"
                    : "bg-stone-900 text-stone-400 hover:text-stone-200")
                }
                onClick={() => {
                  setMode(m);
                  setErreur("");
                }}
              >
                {m}
              </button>
            ))}
          </div>

          <label className="block">
            <span className="text-stone-300 text-sm">Nom d'utilisateur</span>
            <input
              autoFocus
              className="mt-1 w-full bg-stone-900 border border-stone-700 rounded px-3 py-2 focus:outline-none focus:border-amber-400"
              value={nom}
              onChange={(e) => setNom(e.target.value)}
              placeholder="ex : Alain"
              maxLength={24}
            />
          </label>

          <label className="block">
            <span className="text-stone-300 text-sm">Mot de passe</span>
            <input
              type="password"
              className="mt-1 w-full bg-stone-900 border border-stone-700 rounded px-3 py-2 focus:outline-none focus:border-amber-400"
              value={mdp}
              onChange={(e) => setMdp(e.target.value)}
              maxLength={64}
            />
          </label>

          {mode === "inscription" && (
            <label className="block">
              <span className="text-stone-300 text-sm">Confirmer le mot de passe</span>
              <input
                type="password"
                className="mt-1 w-full bg-stone-900 border border-stone-700 rounded px-3 py-2 focus:outline-none focus:border-amber-400"
                value={mdp2}
                onChange={(e) => setMdp2(e.target.value)}
                maxLength={64}
              />
            </label>
          )}

          {erreur && <p className="text-rose-400 text-xs">⚠️ {erreur}</p>}

          <button
            type="submit"
            disabled={enCours}
            className="w-full px-4 py-2 bg-amber-600 hover:bg-amber-500 disabled:opacity-40 rounded font-medium text-stone-900"
          >
            {enCours ? "…" : mode === "connexion" ? "Se connecter" : "Créer mon compte"}
          </button>
        </form>
      </div>
    </div>
  );
}
