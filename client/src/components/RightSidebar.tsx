import { useState } from "react";
import { DiceRoller } from "./DiceRoller";
import { DungeonView } from "./DungeonView";
import { WorldMap } from "./WorldMap";
import { Bestiary } from "./Bestiary";

type Tab = "des" | "monde" | "donjon" | "bestiaire";

const TAB_LABELS: Record<Tab, string> = {
  des: "Dés",
  monde: "Monde",
  donjon: "Donjon",
  bestiaire: "Bestiaire",
};

export function RightSidebar() {
  const [tab, setTab] = useState<Tab>("des");

  return (
    <aside className="w-80 shrink-0 border-l border-stone-800 bg-stone-900/50 flex flex-col">
      <div className="flex border-b border-stone-800 text-xs">
        {(Object.keys(TAB_LABELS) as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={
              "flex-1 px-1.5 py-2 " +
              (tab === t
                ? "bg-stone-800 text-amber-300 font-medium border-b-2 border-amber-400"
                : "text-stone-400 hover:text-stone-200")
            }
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-auto p-3">
        {tab === "des" && <DiceRoller />}
        {tab === "monde" && <WorldMap />}
        {tab === "donjon" && <DungeonView />}
        {tab === "bestiaire" && <Bestiary />}
      </div>
    </aside>
  );
}
