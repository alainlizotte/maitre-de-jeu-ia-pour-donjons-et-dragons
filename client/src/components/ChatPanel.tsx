// Colonne centrale — fil de discussion + champ de saisie. Rendu Markdown
// des narrations DM, statut "thinking", participants. Les images (salles,
// monstres, scènes) ne s'affichent que dans le panneau latéral droit.

import { useEffect, useRef, useState } from "react";
import { useParty } from "../store";
import { api } from "../api/rest";
import { renderMarkdown } from "../utils/markdown";
import type { ChatMessage } from "../api/types";

interface JetAttenduInfo {
  categorie: string;
  type: string;
  personnage: string;
}

interface ChatPanelProps {
  sendSay: (text: string) => void;
  jetAttendu?: JetAttenduInfo | null;
  sendRoll?: () => void;
}

function MessageView({ m }: { m: ChatMessage }) {
  if (m.role === "user") {
    return (
      <div className="flex flex-col items-end mb-3">
        <div className="text-xs text-stone-500 mb-0.5">
          {m.player ?? "Vous"}
        </div>
        <div className="user-bubble max-w-[85%] whitespace-pre-wrap">
          {m.content}
        </div>
      </div>
    );
  }
  if (m.role === "system") {
    return (
      <div className="my-2 text-center text-xs text-stone-500 max-w-[90%] mx-auto">
        <div
          className="inline-block text-left bg-stone-800/50 rounded px-3 py-2 prose-chat text-stone-400"
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }}
        />
      </div>
    );
  }
  // role === "dm"
  return (
    <div className="mb-3 max-w-[95%]">
      <div className="text-xs text-amber-400 mb-0.5 font-serif">Maître du Jeu</div>
      <div
        className="dm-bubble prose-chat"
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }}
      />
      {m.streaming && (
        <div className="text-xs text-stone-500 thinking mt-1">…</div>
      )}
    </div>
  );
}

export function ChatPanel({ sendSay, jetAttendu, sendRoll }: ChatPanelProps) {
  const messages = useParty((s) => s.messages);
  const thinking = useParty((s) => s.thinking);
  const thinkingLabel = useParty((s) => s.thinkingLabel);
  const participants = useParty((s) => s.participants);
  const wsStatus = useParty((s) => s.wsStatus);
  const player = useParty((s) => s.player);
  const state = useParty((s) => s.state);
  const partieId = useParty((s) => s.partie_id);
  const gameover = state?.game_over === true;
  const [goActionEnCours, setGoActionEnCours] = useState(false);
  const chapitreSuivant = ((state?.quete as { bible?: { chapitre_suivant?: string } }
    | null)?.bible?.chapitre_suivant ?? "") as string;
  const chapitreNum = ((state?.quete as { bible?: { chapitre?: number } }
    | null)?.bible?.chapitre ?? 0) as number;
  const [text, setText] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  // En combat, pendant le tour d'un ennemi (pas un PJ), le champ indique au
  // joueur que c'est l'ennemi qui agit.
  const courant = state?.courant_tour_pour;
  const tourEnnemi =
    state?.phase === "combat" &&
    !!courant &&
    !(state.pj ?? []).some((p) => p.nom === courant);

  // Auto-scroll en bas à chaque nouveau message.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, thinking, thinkingLabel]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || !player || thinking) return;
    sendSay(t);
    setText("");
  };

  return (
    <div className="flex-1 min-h-0 min-w-0 flex flex-col overflow-hidden bg-stone-950">
      <div className="shrink-0 border-b border-stone-800 px-4 py-2 text-xs text-stone-400">
        {participants.length > 0
          ? `Connectés : ${participants.join(", ")}`
          : "Personne d'autre connecté."}
      </div>

      {/* 🛡️ Bandeau hors-ligne : le joueur sait pourquoi ses messages ne
          reçoivent pas de réponse (avant : perte silencieuse, écran mort). */}
      {/* 📖 Campagne en chapitres : le bouton charge le chapitre suivant
          DANS la même partie (XP/fiches conservés) — le petit modèle
          n'appelait jamais scenarios_laelith_charger lui-même. */}
      {!gameover && chapitreSuivant && (
        <div className="shrink-0 bg-indigo-950/70 border-b border-indigo-800 px-4 py-2 text-xs text-indigo-200 flex items-center justify-between gap-2">
          <span>
            📖 Chapitre {chapitreNum || "?"} terminé ? La suite de la
            campagne est prête.
          </span>
          <button
            disabled={goActionEnCours || !partieId}
            className="px-3 py-1 rounded bg-indigo-700 hover:bg-indigo-600 text-white text-xs disabled:opacity-50 shrink-0"
            onClick={async () => {
              if (!partieId) return;
              setGoActionEnCours(true);
              try {
                await api.chapitreSuivant(partieId, player ?? "");
              } finally {
                setGoActionEnCours(false);
              }
            }}
          >
            📖 Chapitre suivant
          </button>
        </div>
      )}

      {/* 💀 Game over : boutons déterministes (aucun tour MJ ne tourne
          plus — le garde serveur refuse les tours hors résurrection). */}
      {gameover && (
        <div className="shrink-0 bg-stone-900/90 border-b border-stone-700 px-4 py-3 text-sm text-stone-200">
          <div className="font-serif text-amber-300 mb-2">
            💀 Tous les héros sont tombés.
          </div>
          <div className="flex gap-2 flex-wrap">
            <button
              disabled={goActionEnCours || !partieId}
              className="px-3 py-1.5 rounded bg-amber-700 hover:bg-amber-600 text-white text-xs disabled:opacity-50"
              onClick={async () => {
                if (!partieId) return;
                setGoActionEnCours(true);
                try {
                  await api.gameOverResurrection(partieId, player ?? "");
                } finally {
                  setGoActionEnCours(false);
                }
              }}
            >
              ✨ Résurrection négociée (−1 niveau)
            </button>
            <button
              disabled={goActionEnCours || !partieId}
              className="px-3 py-1.5 rounded bg-stone-700 hover:bg-stone-600 text-white text-xs disabled:opacity-50"
              onClick={async () => {
                if (!partieId) return;
                setGoActionEnCours(true);
                try {
                  await api.gameOverNouvellePartie(partieId);
                } finally {
                  setGoActionEnCours(false);
                }
              }}
            >
              🔄 Nouvelle partie
            </button>
          </div>
        </div>
      )}

      {wsStatus === "reconnecting" && (
        <div className="shrink-0 bg-rose-900/60 border-b border-rose-800 px-4 py-2 text-xs text-rose-200">
          ⚠️ Connexion perdue — reconnexion en cours… Les messages envoyés
          partiront automatiquement au retour de la liaison.
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden chat-scroll p-4">
        {messages.map((m) => (
          <MessageView key={m.id} m={m} />
        ))}
        {thinking && (
          <div className="text-xs text-stone-500 thinking mb-3">
            {thinkingLabel}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* R7 : bandeau Jet demande - visible au-dessus du champ, peu
          importe le panneau affiche (chat ou outils). */}
      {jetAttendu && (
        <div className="shrink-0 bg-amber-900/60 border-t border-amber-500 px-4 py-2">
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm text-amber-200">
              Jet de {jetAttendu.type} attendu ({jetAttendu.personnage})
            </div>
            <button
              onClick={sendRoll}
              className="shrink-0 px-4 py-1.5 rounded bg-amber-600 font-bold text-stone-900 hover:bg-amber-500"
            >
              Lancer
            </button>
          </div>
        </div>
      )}
      <form onSubmit={submit} className="shrink-0 border-t border-stone-800 p-3 flex gap-2">
        <input
          className="flex-1 bg-stone-800 border border-stone-700 rounded px-3 py-2 focus:outline-none focus:border-amber-400 disabled:opacity-40"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={
            thinking
              ? "Le MJ réfléchit…"
              : !player
                ? "Saisissez votre pseudo à l'accueil d'abord…"
                : tourEnnemi
                  ? "Action de l'ennemi"
                  : "Que faites-vous ?"
          }
          disabled={!player || thinking}
        />
        <button
          type="submit"
          className="px-4 py-2 bg-amber-600 hover:bg-amber-500 disabled:opacity-40 rounded font-medium text-stone-900"
          disabled={!player || !text.trim() || thinking}
        >
          Envoyer
        </button>
      </form>
    </div>
  );
}
