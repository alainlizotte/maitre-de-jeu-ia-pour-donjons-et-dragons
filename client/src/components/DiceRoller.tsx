// Dé visuel client — jet rapide d1d20/d6/d8/d100 pour le joueur. Le résultat
// est annoncé dans le chat de partie (visible du MJ et des autres joueurs) ;
// les jets officiels (attaques, sauvegardes) restent lancés par le MJ via
// tool-calling serveur.

import { useState } from "react";

const FACES = [20, 6, 8, 100] as const;

export function DiceRoller({ sendSay }: { sendSay?: (text: string) => void }) {
  const [sides, setSides] = useState<(typeof FACES)[number]>(20);
  const [result, setResult] = useState<number | null>(null);
  const [log, setLog] = useState<string[]>([]);

  const roll = () => {
    const r = 1 + Math.floor(Math.random() * sides);
    setResult(r);
    const line = `1d${sides} → ${r}${r === 20 ? " ⭐" : r === 1 ? " 💀" : ""}`;
    setLog((l) => [line, ...l].slice(0, 12));
    // Annonce le jet dans le chat de partie (informe le MJ et l'équipe).
    sendSay?.(`🎲 Jet manuel : 1d${sides} → ${r}${r === 20 ? " (20 naturel !)" : r === 1 ? " (1 naturel…)" : ""}`);
  };

  return (
    <div className="flex flex-col items-center text-center">
      <div className="mb-3 flex gap-2">
        {FACES.map((n) => (
          <button
            key={n}
            onClick={() => setSides(n)}
            className={
              "px-2 py-1 rounded text-sm " +
              (sides === n
                ? "bg-amber-600 text-stone-900 font-medium"
                : "bg-stone-800 text-stone-300")
            }
          >
            d{n}
          </button>
        ))}
      </div>
      <button
        onClick={roll}
        className="w-24 h-24 rounded-full bg-stone-800 border-2 border-amber-500 text-3xl font-bold text-amber-200 hover:bg-stone-700 active:scale-95"
        title="Lancer"
      >
        {result ?? "?"}
      </button>
      <div className="mt-3 text-stone-500 text-xs">
        {sendSay
          ? `Lance 1d${sides} et annonce le résultat au MJ`
          : `Lance 1d${sides} (visuel local)`}
      </div>
      {log.length > 0 && (
        <ul className="mt-3 text-stone-400 text-xs space-y-0.5 self-stretch">
          {log.map((l, i) => (
            <li key={i} className="font-mono border-b border-stone-800 pb-0.5">{l}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
