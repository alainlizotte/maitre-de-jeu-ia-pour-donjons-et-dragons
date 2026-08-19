// Dé visuel client — jet rapide d'1d20 (information). Le MJ lance les vrais
// jets via tool-calling serveur ; ceci n'est qu'un aide-mémoire joueur.

import { useState } from "react";

const FACES = { 20: "dice-d20", 6: "dice-d6", 100: "dice-d100", 8: "dice-d8" } as const;

export function DiceRoller() {
  const [sides, setSides] = useState<keyof typeof FACES>(20);
  const [result, setResult] = useState<number | null>(null);
  const [log, setLog] = useState<string[]>([]);

  const roll = () => {
    const r = 1 + Math.floor(Math.random() * sides);
    setResult(r);
    const line = `1d${sides} → ${r}${r === 20 ? " ⭐" : r === 1 ? " 💀" : ""}`;
    setLog((l) => [line, ...l].slice(0, 12));
  };

  return (
    <div className="flex flex-col items-center text-center">
      <div className="mb-3 flex gap-2">
        {(Object.keys(FACES) as unknown as (keyof typeof FACES)[]).map((n) => (
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
        Cliquez pour lancer 1d{sides} (visuel joueur)
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
