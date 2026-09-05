// Colonne centrale — fil de discussion + champ de saisie. Rendu Markdown
// des narrations DM (+ images /tool_event), statut "thinking", participants.

import { useEffect, useRef, useState } from "react";
import { useParty } from "../store";
import { renderMarkdown } from "../utils/markdown";
import type { ChatMessage } from "../api/types";

interface ChatPanelProps {
  sendSay: (text: string) => void;
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
        {m.image ? (
          <img
            src={m.image}
            alt={m.content}
            className="max-h-48 mx-auto rounded shadow mb-1"
          />
        ) : null}
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
      {m.image && !m.streaming && (
        <img
          src={m.image}
          alt="illustration"
          className="mt-2 max-h-60 rounded shadow"
        />
      )}
    </div>
  );
}

export function ChatPanel({ sendSay }: ChatPanelProps) {
  const messages = useParty((s) => s.messages);
  const thinking = useParty((s) => s.thinking);
  const thinkingLabel = useParty((s) => s.thinkingLabel);
  const participants = useParty((s) => s.participants);
  const player = useParty((s) => s.player);
  const state = useParty((s) => s.state);
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
