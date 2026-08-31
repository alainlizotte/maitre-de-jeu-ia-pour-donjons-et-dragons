import { xpProgression, xpMinNiveau } from "../lib/xp";

function pctBorne(p: number): number {
  return Math.min(100, Math.max(0, p));
}

type CouleurMode = "auto" | "defaut";

/** Barre de progression XP : niveau actuel → niveau suivant (règles 3.5). */
export function XpBar({
  xp,
  niveau,
  compact = false,
  couleur = "auto",
}: {
  xp?: number;
  niveau: number;
  compact?: boolean;
  couleur?: CouleurMode;
}) {
  const { dans, prochain, requis, pct } = xpProgression(xp ?? 0, niveau);
  const fill = couleur === "auto" ? "bg-violet-500" : "bg-violet-400";
  return (
    <div className={compact ? "text-[10px] leading-tight" : "text-xs"}>
      {!compact && (
        <div className="flex items-end justify-between mb-0.5">
          <span className="text-stone-400 tabular-nums">
            {xp ?? 0} XP
          </span>
          <span className="text-stone-500 tabular-nums">
            prochain : {xpMinNiveau(niveau + 1).toLocaleString("fr-FR")}
          </span>
        </div>
      )}
      <div className="flex items-center gap-2">
        <div className="h-1.5 flex-1 rounded-full bg-stone-800 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${fill}`}
            style={{ width: `${pctBorne(pct)}%` }}
          />
        </div>
        {compact && (
          <span className="text-stone-400 tabular-nums whitespace-nowrap">
            {xp ?? 0}/{requis.toLocaleString("fr-FR")}
          </span>
        )}
      </div>
      {!compact && (
        <div className="mt-0.5 text-[10px] text-stone-500 tabular-nums">
          {dans.toLocaleString("fr-FR")}/{requis.toLocaleString("fr-FR")} XP vers le
          niveau {niveau + 1} ({pct.toFixed(0)}%)
        </div>
      )}
    </div>
  );
}

/** Barre de charge transportée (kg) rapportée à la charge max, avec statut. */
export function ChargeBar({
  poids,
  chargeMax,
  etat,
}: {
  poids?: number;
  chargeMax?: number;
  etat?: string;
}) {
  const max = chargeMax && chargeMax > 0 ? chargeMax : 0;
  const p = Number(poids) || 0;
  const pct = max > 0 ? Math.min(100, (p / max) * 100) : 0;
  const depasse = p > max;
  const lourde = !depasse && max > 0 && p > (2 * max) / 3;
  const cat = etat
    ? etat.charAt(0).toUpperCase() + etat.slice(1).toLowerCase()
    : depasse
      ? "Dépassée"
      : lourde
        ? "Lourde"
        : max === 0
          ? "—"
          : "Légère/Moyenne";
  const fill = depasse
    ? "bg-rose-500"
    : lourde
      ? "bg-amber-400"
      : "bg-emerald-500";
  return (
    <div className="text-xs">
      <div className="flex items-end justify-between mb-0.5">
        <span className="text-stone-400 tabular-nums">
          {max > 0 ? `${p.toFixed(1)}/${max} kg` : `${p.toFixed(1)} kg`}
        </span>
        <span
          className={`px-1.5 py-px rounded text-[10px] font-semibold ${
            depasse
              ? "bg-rose-600 text-white"
              : lourde
                ? "bg-amber-500 text-black"
                : "bg-stone-700 text-stone-300"
          }`}
        >
          {cat}
        </span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-stone-800 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${fill}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {depasse && (
        <p className="text-[10px] text-rose-300 mt-0.5">
          ⚠️ Charge dépassée — limites de mouvement selon PHB 3.5.
        </p>
      )}
    </div>
  );
}
