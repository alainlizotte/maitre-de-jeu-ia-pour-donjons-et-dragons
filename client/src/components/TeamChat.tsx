import { useEffect, useRef, useState } from "react";
import { useParty } from "../store";
import { useChatSocket } from "../hooks/useChatSocket";

interface TeamChatProps {
  sendTeamSay: (text: string) => void;
}

export function TeamChat({ sendTeamSay }: TeamChatProps) {
  const teamMessages = useParty((s) => s.teamMessages);
  const player = useParty((s) => s.player);
  const [text, setText] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [teamMessages]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || !player) return;
    sendTeamSay(t);
    setText("");
  };

  return (
    <div className="flex flex-col h-full bg-stone-900">
      <div className="px-3 py-2 border-b border-stone-700 text-xs text-stone-400">
        💬 Chat d'équipe — visible par les joueurs, pas par le MJ
      </div>
      <div className="flex-1 overflow-auto p-3 space-y-2">
        {teamMessages.length === 0 && (
          <p className="text-stone-500 text-xs italic text-center mt-4">
            Aucun message. Discutez avec vos coéquipiers !
          </p>
        )}
        {teamMessages.map((m, i) => (
          <div key={i} className="text-sm">
            <span className="text-amber-300 font-medium">{m.player}</span>
            <span className="text-stone-500 text-xs ml-1">
              {m.ts ? new Date(m.ts).toLocaleTimeString("fr", { hour: "2-digit", minute: "2-digit" }) : ""}
            </span>
            <div className="text-stone-200 ml-1">{m.text}</div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <form onSubmit={submit} className="border-t border-stone-700 p-2 flex gap-2">
        <input
          className="flex-1 bg-stone-800 border border-stone-600 rounded px-2 py-1.5 text-sm focus:outline-none focus:border-amber-400"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Message équipe…"
          disabled={!player}
        />
        <button
          type="submit"
          className="px-3 py-1.5 bg-amber-700 hover:bg-amber-600 disabled:opacity-40 rounded text-sm font-medium text-stone-900"
          disabled={!player || !text.trim()}
        >
          Envoyer
        </button>
      </form>
    </div>
  );
}
