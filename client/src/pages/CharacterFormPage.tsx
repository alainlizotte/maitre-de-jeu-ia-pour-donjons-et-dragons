// Formulaire de création / édition de personnage D&D 3.5 (en français).
// - Calculs automatiques en direct : mods, ajustements raciaux, PV, CA, BBA,
//   sauvegardes, initiative (miroir exact de server/persos.py).
// - Bouton « Tirage aléatoire » optionnel (4d6 garder les 3 meilleurs, via API).
// - À l'enregistrement : le serveur recalcule tout et lance la génération du
//   portrait (fiche + traits de la race) attribué à ce personnage précis.

import { useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getToken } from "../api/rest";
import type {
  ArmeModele,
  ArmureModele,
  CaracCle,
  CaracMap,
  ClasseModele,
  DonModele,
  DieuModele,
  EspeceModele,
  FichePerso,
  ModelePerso,
  ProficiencesClasse,
  RaceModele,
  SortModele,
} from "../api/types";
import { slugify } from "../utils/slug";
import * as sortsLib from "../lib/sorts";

const CARACS: CaracCle[] = ["FOR", "DEX", "CON", "INT", "SAG", "CHA"];
const LIBELLES_CARACS: Record<CaracCle, string> = {
  FOR: "Force",
  DEX: "Dextérité",
  CON: "Constitution",
  INT: "Intelligence",
  SAG: "Sagesse",
  CHA: "Charisme",
};

// --------------------------------------------------------------------------- //
//  Règles 3.5 côté client (identiques au serveur)
// --------------------------------------------------------------------------- //
const modCarac = (v: number) => Math.floor((v - 10) / 2);
const babParNiveau = (prog: ClasseModele["bab"], niv: number) =>
  prog === "bon" ? niv : prog === "moyen" ? Math.floor((niv * 3) / 4) : Math.floor(niv / 2);
const saveBase = (bonne: boolean, niv: number) => (bonne ? 2 + Math.floor(niv / 2) : Math.floor(niv / 3));

const fmtBonus = (n: number) => (n > 0 ? `+${n}` : String(n));

/** Miroir de persos.charge_maximale() : capacité de charge PHB 3.5 p.162
 *  (livres convertis en kg) pondérée par la catégorie de taille. */
const CHARGE_MAX_LB: Record<number, number> = {
  1: 10, 2: 20, 3: 30, 4: 40, 5: 50, 6: 60, 7: 70, 8: 80, 9: 90,
  10: 100, 11: 115, 12: 130, 13: 150, 14: 175, 15: 200, 16: 230,
  17: 266, 18: 306, 19: 346, 20: 400, 21: 460, 22: 520, 23: 600,
  24: 700, 25: 800, 26: 900, 27: 1040, 28: 1180, 29: 1320,
};
const CHARGE_MULT_TAILLE: Record<string, number> = { P: 0.75, M: 1, G: 2, T: 4, C: 8 };

function chargeMaximale(forValeur: number, taille = "M"): number {
  const f = Math.max(1, Math.floor(forValeur || 1));
  const lb =
    f <= 29 ? (CHARGE_MAX_LB[f] ?? 0) : (CHARGE_MAX_LB[29] ?? 0) * Math.pow(4, Math.floor((f - 29 + 9) / 10));
  const mult = CHARGE_MULT_TAILLE[(taille || "M").toUpperCase()] ?? 1;
  return Math.round(lb * 0.4536 * mult);
}

/** Miroir de persos.dieux_disponibles() : un dieu accepte le perso si sa race
 *  ou sa classe figure parmi ses serviteurs (listes vides = ouvertes), et les
 *  dieux « mal » exigent un alignement mauvais. */
function dieuEligible(d: DieuModele, race: string, classe: string, alignement: string): boolean {
  const parRace = d.races.length > 0 && d.races.includes(race);
  const parClasse = d.classes.length > 0 && d.classes.includes(classe);
  if (!parRace && !parClasse) return false;
  if (d.mal && !alignement.toLowerCase().includes("mauvais")) return false;
  return true;
}

/** Miroir de catalogue.armes_disponibles(). */
function armesDisponibles(
  prof: ProficiencesClasse | undefined,
  armes: ArmeModele[],
): Set<string> {
  if (!prof) return new Set(armes.map((a) => a.nom)); // pas de classe → tout montrer
  const dispo = new Set<string>(prof.specifiques);
  for (const a of armes) if (prof.groupes.includes(a.groupe)) dispo.add(a.nom);
  return dispo;
}

/** Miroir de catalogue.armures_disponibles(). */
function armuresDisponibles(
  prof: ProficiencesClasse | undefined,
  armures: ArmureModele[],
): Set<string> {
  if (!prof) return new Set(armures.map((x) => x.nom));
  const cats = new Set<string>(prof.armures);
  return new Set(
    armures
      .filter((x) => cats.has(x.categorie) || (prof.boucliers && x.categorie === "Bouclier"))
      .map((x) => x.nom),
  );
}

/** Miroir de catalogue.don_disponible() : prereq sur carac. finales + BBA. */
function donDisponible(d: DonModele, final: CaracMap, bab: number): boolean {
  if ((d.prereq.for ?? 0) > final.FOR) return false;
  if ((d.prereq.dex ?? 0) > final.DEX) return false;
  if ((d.prereq.int ?? 0) > final.INT) return false;
  if ((d.prereq.sag ?? 0) > final.SAG) return false;
  if ((d.prereq.bab ?? 0) > bab) return false;
  return true;
}

/** Miroir de fiches._bonus_dons_pv() : bonus de PV apporté par les dons
 *  (« Dur à cuire » = +3 PV ; « Vigueur surhumaine » / « Robustesse » =
 *  +1 PV par niveau), insensible à la casse et aux accents. */
function bonusPvDons(dons: string[], niveau: number): number {
  const norm = (s: string) =>
    s
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[-']/g, " ")
      .trim();
  let bonus = 0;
  for (const d of dons) {
    const nom = norm(d || "");
    if (!nom) continue;
    if (
      ["dur a cuire", "toughness", "resilient", "endurci", "coriace"].some(
        (k) => nom.includes(k),
      )
    ) {
      bonus += 3;
    } else if (
      [
        "vigueur surhumaine",
        "improved toughness",
        "grande robustesse",
        "vigueur",
        "robustesse",
        "endurance supreme",
      ].some((k) => nom.includes(k))
    ) {
      bonus += Math.max(1, niveau);
    }
  }
  return bonus;
}

const fmtPo = (n: number) => (n === 0 ? "gratuit" : `${n} po`);

/** Parse le champ libre « une ligne = un objet « Nom x2 » pour les quantités ». */
const parseLibres = (texte: string): { nom: string; qte: number }[] =>
  texte
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((ligne) => {
      const mx = ligne.match(/^(.*?)\s+x(\d+)$/i);
      return mx ? { nom: mx[1].trim(), qte: parseInt(mx[2], 10) } : { nom: ligne, qte: 1 };
    });

interface FormState {
  nom: string;
  race: string;
  classe: string;
  niveau: number;
  alignement: string;
  dieu: string;
  carac: CaracMap;
  /** +1 de caractéristique des niveaux multiples de 4 (baked dans carac à l'enregistrement). */
  gainCarac: CaracCle | "";
  or: number;
  armesChoisies: string[];
  armuresChoisies: string[];
  equipChoisi: string[];
  equipLibre: string;       // objets hors catalogue, une ligne = un objet
  donsChoisis: string[];
  donsLibre: string;        // dons libres hérités / personnalisés
  sortsConnus: string[];    // sorts connus / grimoire (classes lanceuses)
  familierEspece: string;   // espèce du familier / compagnon animal (PHB 3.5)
  competencesRangs: Record<string, number>;
  histoire: string;
  sexe: string;
  age: string;
  taille: string;
  poids: string;
  yeux: string;
  cheveux: string;
  peau: string;
  description: string;
}

const FORM_VIDE: FormState = {
  nom: "",
  race: "",
  classe: "",
  niveau: 1,
  alignement: "",
  dieu: "",
  carac: { FOR: 0, DEX: 0, CON: 0, INT: 0, SAG: 0, CHA: 0 },
  gainCarac: "",
  or: 0,
  armesChoisies: [],
  armuresChoisies: [],
  equipChoisi: [],
  equipLibre: "",
  donsChoisis: [],
  donsLibre: "",
  sortsConnus: [],
  familierEspece: "",
  competencesRangs: {},
  histoire: "",
  sexe: "",
  age: "",
  taille: "",
  poids: "",
  yeux: "",
  cheveux: "",
  peau: "",
  description: "",
};

/** Valeurs de saisie = valeurs finales − ajustements raciaux (édition). */
function caracBaseDepuisFiche(fiche: FichePerso, races: RaceModele[]): CaracMap {
  const mods = races.find((r) => r.nom === fiche.race)?.mods ?? {};
  const base = {} as CaracMap;
  for (const c of CARACS) {
    base[c] = (fiche.carac?.[c] ?? 10) - (mods[c] ?? 0);
  }
  return base;
}

function champClasse(
  label: string,
  value: string,
  onChange: (v: string) => void,
  placeholder = "",
  type = "text",
): React.ReactElement {
  return (
    <label className="block">
      <span className="text-stone-400 text-xs">{label}</span>
      <input
        type={type}
        className="mt-0.5 w-full bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-amber-400"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}

export function CharacterFormPage() {
  const { slug } = useParams<{ slug?: string }>();
  const modeEdition = Boolean(slug && slug !== "nouveau");
  const navigate = useNavigate();
  const qc = useQueryClient();

  const modele = useQuery({
    queryKey: ["modelePerso"],
    queryFn: api.modelePerso,
    staleTime: Infinity,
  });
  const ficheExistante = useQuery({
    queryKey: ["perso", slug],
    queryFn: () => api.getPerso(slug!),
    enabled: modeEdition,
    retry: false,
  });

  const [form, setForm] = useState<FormState>(FORM_VIDE);
  const [erreur, setErreur] = useState("");
  const [succes, setSucces] = useState(false);
  // L'or saisi manuellement n'est plus écrasé par le tirage automatique.
  const [orModifie, setOrModifie] = useState(false);

  // Non connecté → page de connexion (après les hooks : règles des hooks).
  if (!getToken()) {
    return <Navigate to="/connexion" replace />;
  }

  // Pré-remplissage en mode édition : on attend la fiche ET les catalogues
  // (les ajustements raciaux sont retirés pour retrouver les valeurs saisies).
  useEffect(() => {
    const f = ficheExistante.data;
    if (!modeEdition || !f || ficheExistante.isLoading) return;
    if (!modele.data) return;
    const m = modele.data;
    // Équipement : les objets connus des catalogues cochent les listes, le
    // reste (libellés libres d'anciennes fiches) va dans la zone « libre ».
    const armes = new Set(m.armes.map((a) => a.nom));
    const armures = new Set(m.armures.map((a) => a.nom));
    const equip = new Set(m.equipement_aventurier.map((o) => o.nom));
    const armesChoisies: string[] = [];
    const armuresChoisies: string[] = [];
    const equipChoisi: string[] = [];
    const libres: string[] = [];
    for (const o of f.equipement ?? []) {
      if (armes.has(o.nom)) armesChoisies.push(o.nom);
      else if (armures.has(o.nom)) armuresChoisies.push(o.nom);
      else if (equip.has(o.nom)) equipChoisi.push(o.nom);
      else libres.push(o.qte && o.qte > 1 ? `${o.nom} x${o.qte}` : o.nom);
    }
    const donsCatalogue = new Set(m.dons.map((d) => d.nom.toLowerCase()));
    const donsChoisis: string[] = [];
    const donsLibres: string[] = [];
    for (const d of f.dons ?? []) {
      if (donsCatalogue.has(d.toLowerCase())) donsChoisis.push(m.dons.find((x) => x.nom.toLowerCase() === d.toLowerCase())!.nom);
      else donsLibres.push(d);
    }
    setForm({
      nom: f.nom ?? "",
      race: f.race ?? "",
      classe: f.classe ?? "",
      niveau: f.niveau ?? 1,
      alignement: f.alignement ?? "",
      dieu: (f as Partial<FichePerso>).dieu ?? "",
      carac: caracBaseDepuisFiche(f, m.races),
      gainCarac: "",
      or: f.or ?? 0,
      armesChoisies,
      armuresChoisies,
      equipChoisi,
      equipLibre: libres.join("\n"),
      donsChoisis,
      donsLibre: donsLibres.join("\n"),
      sortsConnus: [...(f.sorts?.connus ?? [])],
      familierEspece: f.familier?.espece ?? "",
      competencesRangs: { ...(f.competences ?? {}) },
      histoire: f.histoire ?? "",
      sexe: f.apparence?.sexe ?? "",
      age: f.apparence?.age ?? "",
      taille: f.apparence?.taille_physique ?? "",
      poids: f.apparence?.poids ?? "",
      yeux: f.apparence?.yeux ?? "",
      cheveux: f.apparence?.cheveux ?? "",
      peau: f.apparence?.peau ?? "",
      description: f.apparence?.description ?? "",
    });
    setOrModifie(true); // ne pas écraser l'or existant par un tirage
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ficheExistante.data, modeEdition, modele.data]);

  const set = <K extends keyof FormState>(cle: K, valeur: FormState[K]) =>
    setForm((f) => ({ ...f, [cle]: valeur }));
  const setCarac = (cle: CaracCle, valeur: number) =>
    setForm((f) => ({ ...f, carac: { ...f.carac, [cle]: valeur } }));

  // ------------------------- Calculs automatiques ------------------------- //
  const raceModele = useMemo(
    () => (modele.data?.races ?? []).find((r) => r.nom === form.race),
    [modele.data, form.race],
  );
  const classeModele = useMemo(
    () => (modele.data?.classes ?? []).find((c) => c.nom === form.classe),
    [modele.data, form.classe],
  );

  // Capacités visibles : traits raciaux (dès le niveau 1) + capacités de
  // classe acquises au niveau choisi (tableaux de classe PHB 3.5).
  const capacites = useMemo(() => {
    const race = (raceModele?.capacites ?? []).map((c) => ({ ...c, source: "Race" as const }));
    const classe = (classeModele?.capacites ?? [])
      .filter((c) => (c.niveau ?? 1) <= form.niveau)
      .map((c) => ({ ...c, source: "Classe" as const }));
    return { race, classe };
  }, [raceModele, classeModele, form.niveau]);

  // Dieux acceptant le personnage comme serviteur (filtre race/classe/alignement).
  const dieuxEligibles = useMemo(
    () =>
      (modele.data?.dieux ?? []).filter((d) =>
        dieuEligible(d, form.race, form.classe, form.alignement),
      ),
    [modele.data, form.race, form.classe, form.alignement],
  );

  // Si le dieu choisi n'est plus éligible (changement race/classe/alignement),
  // on le désélectionne — sauf valeur libre héritée d'une ancienne fiche.
  useEffect(() => {
    if (!modele.data || !form.dieu) return;
    const connu = (modele.data.dieux ?? []).some(
      (d) => d.nom.toLowerCase() === form.dieu.toLowerCase(),
    );
    if (
      connu &&
      !dieuxEligibles.some((d) => d.nom.toLowerCase() === form.dieu.toLowerCase())
    ) {
      set("dieu", "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dieuxEligibles, modele.data, form.dieu]);

  const calc = useMemo(() => {
    const modsRace = raceModele?.mods ?? {};
    const final = {} as CaracMap;
    for (const c of CARACS) {
      final[c] = Math.max(
        1,
        (form.carac[c] || 10) + (modsRace[c] ?? 0) + (form.gainCarac === c ? 1 : 0),
      );
    }
    const mods = {} as CaracMap;
    for (const c of CARACS) mods[c] = modCarac(final[c]);

    const dv = classeModele?.de_vie ?? 10;
    const modCon = mods.CON;
    const pvNiveauxSuivants =
      Math.max(0, form.niveau - 1) * (Math.floor(dv / 2) + 1 + modCon);
    // Dons choisis + libres : certains (« Dur à cuire »…) ajoutent des PV.
    const donsTous = [
      ...form.donsChoisis,
      ...form.donsLibre.split("\n").map((l) => l.trim()).filter(Boolean),
    ];
    const bonusDons = bonusPvDons(donsTous, form.niveau);
    const pvMax = Math.max(1, dv + modCon + pvNiveauxSuivants + bonusDons);

    // CA 3.5 : 10 + meilleure armure portée + meilleur bouclier + Dex
    // plafonnée par le dex_max de l'armure (miroir de persos.calculer_ca_armure).
    const armuresPortees = (modele.data?.armures ?? []).filter((a) =>
      form.armuresChoisies.includes(a.nom),
    );
    const corps = armuresPortees.filter((a) => a.categorie !== "Bouclier");
    const boucliers = armuresPortees.filter((a) => a.categorie === "Bouclier");
    const meilleureArmure = corps.reduce<ArmureModele | null>(
      (best, a) => (!best || a.ca > best.ca ? a : best),
      null,
    );
    const meilleurBouclier = boucliers.reduce<ArmureModele | null>(
      (best, a) => (!best || a.ca > best.ca ? a : best),
      null,
    );
    const dexMax = meilleureArmure?.dex_max ?? 99;
    const ca =
      10 +
      (meilleureArmure?.ca ?? 0) +
      (meilleurBouclier?.ca ?? 0) +
      Math.min(mods.DEX, dexMax);

    const bonnes = new Set(classeModele?.sauves_bonnes ?? []);
    const sauves = {
      Vigueur: saveBase(bonnes.has("Vigueur"), form.niveau) + modCon,
      Reflexes: saveBase(bonnes.has("Reflexes"), form.niveau) + mods.DEX,
      Volonte: saveBase(bonnes.has("Volonte"), form.niveau) + mods.SAG,
    };
    return {
      final,
      mods,
      modsRace,
      pvMax,
      bonusDons,
      ca,
      bab: babParNiveau(classeModele?.bab ?? "moyen", form.niveau),
      sauves,
      initiative: mods.DEX,
      chargeMax: chargeMaximale(final.FOR, raceModele?.taille ?? "M"),
      complet: Boolean(raceModele && classeModele),
    };
  }, [raceModele, classeModele, form.carac, form.gainCarac, form.niveau, form.armuresChoisies, form.donsChoisis, form.donsLibre, modele.data]);

  // ------------------------- Magie (sorts 3.5) ----------------------------
  // Miroir de server/sorts.py : castable, emplacements/jour, budgets connus.
  const magie = useMemo(() => {
    const tables = modele.data;
    const lanceur = sortsLib.estLanceur(tables, form.classe);
    const nls = lanceur ? sortsLib.niveauSortMax(tables, form.classe, form.niveau) : -1;
    const cle = tables?.sorts_carac?.[form.classe] ?? "INT";
    const mod = sortsLib.modCarac(calc.final[cle as CaracCle] ?? 10);
    const slots = lanceur
      ? sortsLib.emplacements(tables, form.classe, form.niveau, mod)
      : ({} as Record<number, number>);
    const budgetConnus = sortsLib.sortsConnusMax(tables, form.classe, form.niveau);
    const liste = lanceur ? sortsLib.sortsDisponibles(tables, form.classe, nls) : [];
    // Grimoire du magicien : départ 3 + mod INT sorts de niveau ≥ 1 (tous les
    // tours sont connus d'office) + 2 sorts par niveau de magicien gagné.
    const budgetGrimoire =
      form.classe === "Magicien"
        ? 3 + sortsLib.modCarac(calc.final.INT) + 2 * Math.max(0, form.niveau - 1)
        : 0;
    return { lanceur, nls, cle, slots, budgetConnus, liste, budgetGrimoire };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modele.data, form.classe, form.niveau, calc]);

  // Excès de sorts connus (ex. édition d'une fiche contenant plus de sorts
  // que le budget de son niveau) — avertissement en direct au-dessus de la
  // validation à l'enregistrement.
  const excedentSorts = useMemo(() => {
    if (!magie.lanceur) return 0;
    const comptes: Record<number, number> = {};
    for (const nomS of form.sortsConnus) {
      const s = magie.liste.find((x) => x.nom === nomS);
      if (s) comptes[s.niveau] = (comptes[s.niveau] ?? 0) + 1;
    }
    let exces = 0;
    for (const [lvlStr, mx] of Object.entries(magie.budgetConnus)) {
      exces += Math.max(0, (comptes[Number(lvlStr)] ?? 0) - mx);
    }
    if (form.classe === "Magicien") {
      // Grimoire : les sorts de niveau ≥ 1 consomment le budget global
      // (départ 3 + INT, +2 par niveau gagné) — les tours sont d'office connus.
      const horsTours = form.sortsConnus.filter(
        (n) => (magie.liste.find((x) => x.nom === n)?.niveau ?? 0) >= 1,
      ).length;
      exces += Math.max(0, horsTours - magie.budgetGrimoire);
    }
    return exces;
  }, [magie, form.sortsConnus, form.classe, form.niveau]);

  // ---------------- Familier / compagnon animal (PHB 3.5) -----------------
  // Espèces disponibles selon la classe/niveau : familier (Magicien/Sorcier,
  // 10 espèces) ou compagnon animal (Druide niv.1, Rodeur niv.4 — hors-normes
  // incluses quand le niveau de classe effectif suffit). null = pas de droit.
  const especesCompagnon = useMemo<null | {
    type: "familier" | "compagnon";
    liste: EspeceModele[];
  }>(() => {
    const fml = modele.data?.familiers;
    if (!fml) return null;
    if (form.classe === "Magicien" || form.classe === "Sorcier")
      return { type: "familier", liste: fml.familiers };
    const nivMin = fml.niveau_min_compagnon?.[form.classe] ?? 99;
    if (
      (form.classe === "Druide" || form.classe === "Rodeur") &&
      form.niveau >= nivMin
    ) {
      const nivEff =
        form.classe === "Rodeur" ? Math.max(0, form.niveau - 3) : form.niveau;
      const hors = fml.compagnons_hors_norme.filter(
        (h) =>
          form.niveau >= (h.niveau_min ?? 4) &&
          nivEff - (h.reduction ?? 3) >= 1,
      );
      return { type: "compagnon", liste: [...fml.compagnons_animaux, ...hors] };
    }
    return null;
  }, [modele.data, form.classe, form.niveau]);

  // Espèce choisie plus valide (changement de classe/niveau) → désélection.
  useEffect(() => {
    if (
      form.familierEspece &&
      especesCompagnon &&
      !especesCompagnon.liste.some((e) => e.nom === form.familierEspece)
    ) {
      set("familierEspece", "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [especesCompagnon, form.familierEspece]);

  // ------------------- Disponibilités selon la classe --------------------- //
  const profClasse = form.classe ? modele.data?.proficiences?.[form.classe] : undefined;
  const armesDispo = useMemo(
    () => armesDisponibles(profClasse, modele.data?.armes ?? []),
    [profClasse, modele.data],
  );
  const armuresDispo = useMemo(
    () => armuresDisponibles(profClasse, modele.data?.armures ?? []),
    [profClasse, modele.data],
  );
  const competencesDeClasse = useMemo(
    () =>
      new Set(
        (form.classe ? modele.data?.competences_classe?.[form.classe] : undefined) ??
          modele.data?.competences.map((c) => c.nom) ??
          [],
      ),
    [modele.data, form.classe],
  );
  const rangsUtilises = useMemo(
    () => Object.values(form.competencesRangs).reduce((s, r) => s + (r || 0), 0),
    [form.competencesRangs],
  );

  // Total des achats cochés (armes + armures + équipement) et solde d'or.
  const depenseEquipement = useMemo(() => {
    const m = modele.data;
    if (!m) return 0;
    const prix = new Map<string, number>();
    for (const a of m.armes) prix.set(a.nom, a.cout);
    for (const a of m.armures) prix.set(a.nom, a.cout);
    for (const o of m.equipement_aventurier) prix.set(o.nom, o.cout);
    let total = 0;
    for (const nom of [...form.armesChoisies, ...form.armuresChoisies, ...form.equipChoisi]) {
      total += prix.get(nom) ?? 0;
    }
    return total;
  }, [modele.data, form.armesChoisies, form.armuresChoisies, form.equipChoisi]);
  const soldeOr = (Number(form.or) || 0) - depenseEquipement;

  // Poids total porté (kg) des armes/armures/équipements cochés + objets libres
  // connus du catalogue → permet d'indiquer la charge par rapport à la capacité.
  const poidsPorte = useMemo(() => {
    const m = modele.data;
    if (!m) return { poids: 0, inconnus: [] as string[] };
    const poids = new Map<string, number>();
    for (const a of m.armes) poids.set(a.nom, a.poids ?? 0);
    for (const a of m.armures) poids.set(a.nom, a.poids ?? 0);
    for (const o of m.equipement_aventurier) poids.set(o.nom, o.poids ?? 0);
    let total = 0;
    const inconnus: string[] = [];
    for (const nom of [...form.armesChoisies, ...form.armuresChoisies, ...form.equipChoisi]) {
      total += poids.get(nom) ?? 0;
    }
    for (const o of parseLibres(form.equipLibre)) {
      const pu = poids.get(o.nom);
      if (pu === undefined) {
        inconnus.push(o.nom);
        continue;
      }
      total += pu * (o.qte || 1);
    }
    if (form.or) total += ((Number(form.or) || 0) * 10) / 50 * 0.4536;
    return { poids: Math.round(total * 100) / 100, inconnus };
  }, [modele.data, form.armesChoisies, form.armuresChoisies, form.equipChoisi, form.equipLibre, form.or]);
  const etatCharge = useMemo(() => {
    const max = calc.chargeMax || 1;
    const p = poidsPorte.poids;
    const tiers = max / 3;
    if (p > max) return { cat: "Dépassée", depasse: true, max: false, pct: Math.min(100, (p / max) * 100) };
    if (p > 2 * tiers) return { cat: "Lourde", depasse: false, max: true, pct: Math.min(100, (p / max) * 100) };
    if (p > tiers) return { cat: "Moyenne", depasse: false, max: false, pct: Math.min(100, (p / max) * 100) };
    return { cat: "Légère", depasse: false, max: false, pct: Math.min(100, (p / max) * 100) };
  }, [poidsPorte.poids, calc.chargeMax]);
  // Poids de l'or porté (PHB 3.5 : 50 pièces = 1 lb = 0,4536 kg), inclus dans
  // poidsPorte.poids — itemisé dans l'UI pour ne plus paraître fantôme.
  const poidsOrKg = (((Number(form.or) || 0) * 10) / 50) * 0.4536;
  const budgetRangs = useMemo(() => {
    const base = form.classe
      ? (modele.data?.points_competence?.[form.classe] ?? 0)
      : 0;
    if (!base) return 0;
    const parNiveau = Math.max(1, base + calc.mods.INT);
    let total = parNiveau * 4 + Math.max(1, base + calc.mods.INT) * Math.max(0, form.niveau - 1);
    // PHB 3.5, Humain : +4 points au niveau 1, +1 par niveau supplémentaire
    // (l'ancienne formule « +niveau » donnait 9 au lieu de 12 en niv. 1).
    if (form.race === "Humain") total += 4 + Math.max(0, form.niveau - 1);
    return total;
  }, [modele.data, form.classe, form.race, form.niveau, calc.mods.INT]);

  // Budget de dons (règles 3.5) : 1 au niveau 1, puis 1 don supplémentaire
  // aux niveaux 3, 6, 9… ; les humains gagnent 1 don en plus ; le GUERRIER
  // reçoit des dons supplémentaires aux niveaux 1, 2, 4, 6… (1 + niv/2).
  // Les dons libres (zone de texte) consomment le même budget.
  const budgetDons = useMemo(
    () =>
      1 +
      Math.floor(form.niveau / 3) +
      (form.race === "Humain" ? 1 : 0) +
      (form.classe === "Guerrier" ? 1 + Math.floor(form.niveau / 2) : 0),
    [form.niveau, form.race, form.classe],
  );
  const donsLibresUtilises = useMemo(
    () => form.donsLibre.split("\n").map((l) => l.trim()).filter(Boolean).length,
    [form.donsLibre],
  );
  const donsTotal = form.donsChoisis.length + donsLibresUtilises;
  const donsPlein = donsTotal >= budgetDons;

  // Checklist « avancement » : gains attendus au niveau courant (PHB 3.5) —
  // don aux niv. 3/6/9…, +1 caractéristique aux niv. 4/8/12…, nouveaux sorts,
  // rangs de compétence du nouveau niveau. Sert de guide à la montée de
  // niveau via l'édition de la fiche.
  const avancement = useMemo(() => {
    const items: { libelle: string; note: string; etat: "ok" | "a_faire" | "info" }[] = [];
    if (form.niveau < 2) return items;
    if (form.niveau % 4 === 0) {
      items.push({
        libelle: "+1 à une caractéristique",
        note: form.gainCarac
          ? `attribué à ${LIBELLES_CARACS[form.gainCarac]}`
          : "à répartir (sélecteur dans « Caractéristiques »)",
        etat: form.gainCarac ? "ok" : "a_faire",
      });
    }
    const budgetAvant =
      1 + Math.floor((form.niveau - 1) / 3) + (form.race === "Humain" ? 1 : 0);
    if (budgetDons > budgetAvant) {
      items.push({
        libelle: "Don supplémentaire",
        note: `${donsTotal} / ${budgetDons} don(s) choisi(s)`,
        etat: donsTotal >= budgetDons ? "ok" : "a_faire",
      });
    }
    if (magie.lanceur) {
      if (form.classe === "Magicien") {
        const horsTours = form.sortsConnus.filter(
          (n) => (magie.liste.find((x) => x.nom === n)?.niveau ?? 0) >= 1,
        ).length;
        items.push({
          libelle: "Grimoire (+2 sorts par niveau gagné)",
          note: `${horsTours} / ${magie.budgetGrimoire} sorts de niveau ≥ 1`,
          etat: horsTours >= magie.budgetGrimoire ? "ok" : "info",
        });
      } else if (form.classe === "Sorcier" || form.classe === "Barde") {
        items.push({
          libelle: "Nouveaux sorts connus",
          note: "budgets par niveau dans la section Sorts",
          etat: "info",
        });
      } else {
        items.push({
          libelle: "Sorts divins",
          note: "liste de classe complète disponible — mémorisation en jeu",
          etat: "info",
        });
      }
    }
    items.push({
      libelle: "Rangs de compétence",
      note: budgetRangs > 0
        ? `${rangsUtilises} / ${budgetRangs} rangs utilisés`
        : "choisissez une classe",
      etat: budgetRangs <= 0 ? "a_faire" : rangsUtilises >= budgetRangs ? "ok" : "info",
    });
    return items;
  }, [form.niveau, form.race, form.gainCarac, form.classe, form.sortsConnus,
      budgetDons, donsTotal, magie, budgetRangs, rangsUtilises]);

  const toggleListe = (
    cle: "armesChoisies" | "armuresChoisies" | "equipChoisi" | "donsChoisis" | "sortsConnus",
    valeur: string,
  ) =>
    setForm((f) => ({
      ...f,
      [cle]: f[cle].includes(valeur)
        ? f[cle].filter((v) => v !== valeur)
        : [...f[cle], valeur],
    }));

  const toggleCompetence = (nom: string) =>
    setForm((f) => {
      const m = { ...f.competencesRangs };
      if (nom in m) delete m[nom];
      else m[nom] = 0;
      return { ...f, competencesRangs: m };
    });

  const setRangCompetence = (nom: string, rang: number) =>
    setForm((f) => {
      const autres = Object.entries(f.competencesRangs).reduce(
        (s, [n, r]) => (n === nom ? s : s + (r || 0)),
        0,
      );
      const maxPossible = Math.max(0, budgetRangs - autres);
      const v = Math.min(Math.max(0, rang), maxPossible);
      return { ...f, competencesRangs: { ...f.competencesRangs, [nom]: v } };
    });

  // ----------------------------- Mutations -------------------------------- //
  // 🛡️ Garde anti-course (un compteur PAR tirage) : seules les réponses au
  // DERNIER clic de CHAQUE tirage sont appliquées (partie réelle : des
  // réponses arrivées dans le désordre ont laissé dans le formulaire une
  // apparence jamais affichée à l'écran).
  const reqCaracs = useRef(0);
  const reqOr = useRef(0);
  const reqApparence = useRef(0);

  const tirageAleatoire = useMutation({
    mutationFn: async () => {
      const req = ++reqCaracs.current;
      const d = await api.statsAleatoires();
      return { req, d };
    },
    onSuccess: ({ req, d }) => {
      if (req !== reqCaracs.current) return; // réponse périmée
      for (const c of CARACS) {
        setCarac(c, d.carac[c] ?? 10);
      }
    },
  });

  const orDepart = useMutation({
    mutationFn: async (v: { classe: string; mode: "tirage" | "moyenne" }) => {
      const req = ++reqOr.current;
      const d = await api.orDepart(v.classe, v.mode);
      return { req, d };
    },
    onSuccess: ({ req, d }) => {
      if (req !== reqOr.current) return; // réponse périmée
      set("or", d.or);
    },
  });

  // Tirage officiel âge/taille/poids (tables DRS : race × classe × sexe).
  const apparenceAleatoire = useMutation({
    mutationFn: async () => {
      const req = ++reqApparence.current;
      const d = await api.apparenceAleatoire(
        form.race,
        form.classe,
        form.sexe === "F" ? "F" : "M",
      );
      return { req, d };
    },
    onSuccess: ({ req, d }) => {
      if (req !== reqApparence.current) return; // réponse périmée
      set("age", d.age);
      set("taille", d.taille);
      set("poids", d.poids);
    },
  });

  // Changement de classe → tirage automatique de l'or de départ (table PHB),
  // sauf saisie manuelle ou mode édition.
  useEffect(() => {
    if (modeEdition || orModifie || !form.classe) return;
    orDepart.mutate({ classe: form.classe, mode: "tirage" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.classe, modeEdition, orModifie]);

  const enregistrer = useMutation({
    mutationFn: () => {
      // Le serveur recalcule PV/CA/BBA/sauvegardes et régénère le portrait.
      // Le +1 de caractéristique (niveaux multiples de 4) est envoyé à part :
      // le serveur l'applique et le journalise dans `gains_carac`.
      const payload: Record<string, unknown> = {
        nom: form.nom.trim(),
        race: form.race,
        classe: form.classe,
        niveau: form.niveau,
        carac: form.carac,
        gain_carac: form.gainCarac || undefined,
        alignement: form.alignement,
        dieu: form.dieu.trim(),
        or: (Number(form.or) || 0) * 10,
        equipement: [
          ...form.armesChoisies.map((nom) => ({ nom, qte: 1 })),
          ...form.armuresChoisies.map((nom) => ({ nom, qte: 1 })),
          ...form.equipChoisi.map((nom) => ({ nom, qte: 1 })),
          ...parseLibres(form.equipLibre),
        ],
        dons: [
          ...form.donsChoisis,
          ...form.donsLibre.split("\n").map((l) => l.trim()).filter(Boolean),
        ],
        // Magie : connus/grimoire (la préparation quotidienne se fait en jeu).
        sorts: magie.lanceur
          ? {
              connus: [
                // Magicien : tous les tours de magicien sont connus d'office.
                ...(form.classe === "Magicien"
                  ? magie.liste.filter((s) => s.niveau === 0).map((s) => s.nom)
                  : []),
                ...form.sortsConnus,
              ],
              prepares: {},
            }
          : undefined,
        // Familier / compagnon animal : espèce choisie (optionnel). En jeu,
        // le MJ l'appelle via le tool appeler_familier (rituel 100 po).
        familier:
          especesCompagnon && form.familierEspece
            ? { type: especesCompagnon.type, espece: form.familierEspece }
            : undefined,
        competences: Object.fromEntries(
          Object.entries(form.competencesRangs).filter(([, r]) => (r || 0) > 0),
        ),
        histoire: form.histoire.trim(),
        apparence: {
          sexe: form.sexe,
          age: form.age.trim(),
          taille: form.taille.trim(),
          poids: form.poids.trim(),
          yeux: form.yeux.trim(),
          cheveux: form.cheveux.trim(),
          peau: form.peau.trim(),
          description: form.description.trim(),
        },
      };
      return api.savePerso(payload);
    },
    onSuccess: () => {
      setErreur("");
      setSucces(true);
      qc.invalidateQueries({ queryKey: ["persos"] });
      qc.invalidateQueries({ queryKey: ["persos", "detail"] });
      // Laisse le temps de voir le message « portrait en génération… ».
      setTimeout(() => navigate("/"), 2500);
    },
    onError: (err) => setErreur((err as Error).message),
  });

  const soumettre = (e: React.FormEvent) => {
    e.preventDefault();
    setErreur("");
    if (!form.nom.trim()) {
      setErreur("Le nom du personnage est requis.");
      return;
    }
    if (!form.race || !form.classe) {
      setErreur("Choisissez une race et une classe.");
      return;
    }
    // Caractéristiques : uniquement via le tirage aux dés (pas de saisie).
    for (const c of CARACS) {
      const v = form.carac[c];
      if (!Number.isFinite(v) || v < 1 || v > 25) {
        setErreur(
          `Caractéristiques non tirées — utilisez « 🎲 Tirage aléatoire (4d6) » (${LIBELLES_CARACS[c]} manquante).`,
        );
        return;
      }
    }
    // Âge / taille / poids : uniquement via le tirage aux tables officielles.
    if (!form.age.trim() || !form.taille.trim() || !form.poids.trim()) {
      setErreur(
        "Âge, taille et poids doivent être tirés — cliquez sur « 🎲 Tirer âge / taille / poids ».",
      );
      return;
    }
    // Dons : nombre limité selon le niveau (+1 si humain).
    if (donsTotal > budgetDons) {
      setErreur(
        `Trop de dons (${donsTotal}) : maximum ${budgetDons} au niveau ${form.niveau}` +
        (form.race === "Humain" ? ", bonus humain inclus" : "") +
        ".",
      );
      return;
    }
    // Sorts connus : budgets PHB (Sorcier/Barde par niveau, grimoire magicien).
    if (magie.lanceur && (form.classe === "Sorcier" || form.classe === "Barde")) {
      const comptes: Record<number, number> = {};
      for (const nomS of form.sortsConnus) {
        const s = magie.liste.find((x) => x.nom === nomS);
        if (s) comptes[s.niveau] = (comptes[s.niveau] ?? 0) + 1;
      }
      for (const [lvl, mx] of Object.entries(magie.budgetConnus)) {
        if ((comptes[Number(lvl)] ?? 0) > mx) {
          setErreur(
            `Trop de sorts connus de niveau ${lvl} (${comptes[Number(lvl)]}) : ` +
            `maximum ${mx} pour ${form.classe} niv. ${form.niveau}.`,
          );
          return;
        }
      }
    }
    if (magie.lanceur && form.classe === "Magicien") {
      const horsTours = form.sortsConnus.filter(
        (n) => (magie.liste.find((x) => x.nom === n)?.niveau ?? 0) >= 1,
      ).length;
      if (horsTours > magie.budgetGrimoire) {
        setErreur(
          `Grimoire : maximum ${magie.budgetGrimoire} sorts de niveau ≥ 1 ` +
          `(départ 3 + mod INT${form.niveau > 1 ? ` + 2 × ${form.niveau - 1} niveaux gagnés` : ""}) — ` +
          `${horsTours} sélectionnés.`,
        );
        return;
      }
    }
    // Compétences : impossible de dépasser le budget de points.
    if (rangsUtilises > budgetRangs) {
      setErreur(
        `Trop de rangs de compétence (${rangsUtilises}) : maximum ${budgetRangs} au niveau ${form.niveau}.`,
      );
      return;
    }
    // 💰 Or de départ : l'équipement ne peut pas dépasser le solde (partie
    // réelle : 165 po cochés pour 150 po passaient, solde jamais déduit).
    if (soldeOr < 0) {
      setErreur(
        `Équipement trop cher : ${depenseEquipement} po dépensés pour ` +
        `${Number(form.or) || 0} po d'or de départ — retirez un objet ` +
        `(${Math.abs(soldeOr)} po de trop).`,
      );
      return;
    }
    // Avancement en attente : les gains du niveau doivent être consommés
    // (mêmes règles que le serveur — retour immédiat sans aller-retour).
    if (avancementAttente) {
      if (donsTotal < budgetDons) {
        setErreur(
          `Avancement incomplet : il reste ${budgetDons - donsTotal} don(s) à ` +
          `choisir (${donsTotal}/${budgetDons}).`,
        );
        return;
      }
      if (form.niveau % 4 === 0 && !form.gainCarac) {
        setErreur(
          `Avancement incomplet : le niveau ${form.niveau} (multiple de 4) doit ` +
          "recevoir son +1 de caractéristique.",
        );
        return;
      }
    }
    enregistrer.mutate();
  };

  const chargementFiche = modeEdition && ficheExistante.isLoading;

  // Verrou d'avancement : la fiche n'est modifiable que si un passage de
  // niveau est en attente (niveau gagné par XP > dernier niveau confirmé).
  const avancementConfirme = Number(
    (ficheExistante.data as Partial<FichePerso> | undefined)?.avancement_confirme ?? 1,
  );
  const avancementAttente = modeEdition && avancementConfirme < form.niveau;
  const verrouille = modeEdition && !avancementAttente;

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <form onSubmit={soumettre} className="max-w-4xl w-full mx-auto p-6 space-y-6">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => navigate("/")}
            className="px-3 py-1.5 bg-stone-800 hover:bg-stone-700 border border-stone-700 rounded text-sm text-stone-300"
          >
            ← Retour
          </button>
          <h1 className="font-serif text-2xl text-amber-300">
            {modeEdition ? "Modifier le personnage" : "Créer un personnage"}
          </h1>
        </div>

        {chargementFiche ? (
          <p className="text-stone-400">Chargement de la fiche…</p>
        ) : ficheExistante.isError ? (
          <p className="text-rose-400">⚠️ {(ficheExistante.error as Error).message}</p>
        ) : verrouille ? (
          <section className="bg-stone-800/40 border border-amber-700/40 rounded-lg p-8 text-center space-y-3">
            <div className="text-4xl">🔒</div>
            <h2 className="font-serif text-xl text-amber-200">Fiche verrouillée</h2>
            <p className="text-sm text-stone-400 max-w-md mx-auto">
              Les choix d'avancement de <strong>{form.nom}</strong> (niveau{" "}
              {form.niveau}) sont confirmés. La fiche sera de nouveau
              modifiable au prochain passage de niveau — gagnez de l'XP en
              jouant !
            </p>
            <button
              type="button"
              onClick={() => navigate("/")}
              className="px-4 py-2 bg-stone-700 hover:bg-stone-600 rounded text-sm text-stone-200"
            >
              ← Retour
            </button>
          </section>
        ) : (
          <>
            {/* ------------------------- Identité ------------------------- */}
            <section className="bg-stone-800/40 border border-stone-700/60 rounded-lg p-4">
              <h2 className="font-serif text-lg text-amber-200 mb-3">Identité</h2>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="col-span-2">
                  {champClasse("Nom du personnage *", form.nom, (v) => set("nom", v), "ex : Elara des Bois")}
                </div>
                <label className="block">
                  <span className="text-stone-400 text-xs">Race *</span>
                  <select
                    className="mt-0.5 w-full bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-amber-400"
                    value={form.race}
                    onChange={(e) => set("race", e.target.value)}
                  >
                    <option value="">— Choisir —</option>
                    {(modele.data?.races ?? []).map((r) => (
                      <option key={r.nom} value={r.nom}>
                        {r.nom}
                        {Object.keys(r.mods).length > 0 &&
                          ` (${Object.entries(r.mods).map(([k, v]) => `${k} ${v! > 0 ? "+" : ""}${v}`).join(", ")})`}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-stone-400 text-xs">Classe *</span>
                  <select
                    className="mt-0.5 w-full bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-amber-400"
                    value={form.classe}
                    onChange={(e) => set("classe", e.target.value)}
                  >
                    <option value="">— Choisir —</option>
                    {(modele.data?.classes ?? []).map((c) => (
                      <option key={c.nom} value={c.nom}>
                        {c.nom} (d{c.de_vie})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-stone-400 text-xs">Niveau</span>
                  <div
                    className="mt-0.5 w-full bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm text-amber-100"
                    title="Le niveau évolue automatiquement avec l'XP gagnée en jeu"
                  >
                    {form.niveau}{" "}
                    <span className="text-stone-500 text-xs">
                      {modeEdition ? "— gagné par XP" : "— départ"}
                    </span>
                  </div>
                </label>
                <label className="block">
                  <span className="text-stone-400 text-xs">Alignement</span>
                  <select
                    className="mt-0.5 w-full bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-amber-400"
                    value={form.alignement}
                    onChange={(e) => set("alignement", e.target.value)}
                  >
                    <option value="">— Choisir —</option>
                    {(modele.data?.alignements ?? []).map((a) => (
                      <option key={a} value={a}>{a}</option>
                    ))}
                  </select>
                </label>
                <div className="col-span-2">
                  <label className="block">
                    <span className="text-stone-400 text-xs">
                      Dieu / Divinité
                      {form.race || form.classe
                        ? " (selon ses serviteurs)"
                        : " — choisissez d'abord race et classe"}
                    </span>
                    <select
                      className="mt-0.5 w-full bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-amber-400 disabled:opacity-60"
                      value={form.dieu}
                      onChange={(e) => set("dieu", e.target.value)}
                    >
                      <option value="">— Aucun —</option>
                      {/* Valeur libre héritée d'une ancienne fiche : conservée affichable. */}
                      {form.dieu &&
                        !dieuxEligibles.some(
                          (d) => d.nom.toLowerCase() === form.dieu.toLowerCase(),
                        ) && (
                          <option value={form.dieu}>« {form.dieu} » (actuel)</option>
                        )}
                      {dieuxEligibles.map((d) => (
                        <option key={d.nom} value={d.nom}>
                          {d.nom}{d.titre ? `, ${d.titre}` : ""} — {d.alignement}
                          {d.mal ? " (maléfiques)" : ""}
                        </option>
                      ))}
                      {modele.data &&
                        dieuxEligibles.length === 0 &&
                        !form.dieu && (
                          <option value="" disabled>
                            Aucun dieu ne sert cette race/classe/alignement
                          </option>
                        )}
                    </select>
                  </label>
                </div>
              </div>
            </section>

            {/* --------------------- Caractéristiques --------------------- */}
            <section className="bg-stone-800/40 border border-stone-700/60 rounded-lg p-4">
              <div className="flex items-center gap-3 mb-3">
                <h2 className="font-serif text-lg text-amber-200">Caractéristiques</h2>
                <span className="text-xs text-stone-500 italic">aux dés uniquement</span>
                <button
                  type="button"
                  onClick={() => tirageAleatoire.mutate()}
                  disabled={tirageAleatoire.isPending}
                  title="4d6, on garde les 3 meilleurs — pour chaque caractéristique"
                  className="ml-auto px-3 py-1.5 bg-violet-700 hover:bg-violet-600 disabled:opacity-40 rounded text-sm font-medium"
                >
                  🎲 Tirage aléatoire (4d6)
                </button>
              </div>
              {tirageAleatoire.isSuccess && (
                <p className="text-xs text-emerald-400 mb-2">{tirageAleatoire.data.d.methode}</p>
              )}
              <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
                {CARACS.map((c) => {
                  const racial = calc.modsRace[c];
                  const nonTire = !form.carac[c];
                  return (
                    <div
                      key={c}
                      className={`bg-stone-900/60 border rounded p-2 text-center ${
                        nonTire ? "border-stone-800" : "border-stone-700"
                      }`}
                    >
                      <div className="text-xs text-stone-400 mb-1">{LIBELLES_CARACS[c]}</div>
                      {/* Lecture seule : la valeur ne peut venir que des dés. */}
                      <div
                        className={`w-full bg-stone-800 border border-stone-700/50 rounded px-2 py-1 text-lg tabular-nums text-center ${
                          nonTire ? "text-stone-600" : "text-stone-100"
                        }`}
                        title={nonTire ? "Lancez le tirage aux dés" : undefined}
                      >
                        {nonTire ? "?" : form.carac[c]}
                      </div>
                      <div className="text-[11px] mt-1 space-y-0.5">
                        {racial !== undefined && (
                          <div className={racial > 0 ? "text-emerald-400" : "text-rose-400"}>
                            racial {fmtBonus(racial)}
                          </div>
                        )}
                        <div className="text-amber-300">
                          total {calc.final[c]} ({fmtBonus(calc.mods[c])})
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
              {form.niveau >= 4 && form.niveau % 4 === 0 && (
                <label className="block mt-3 max-w-xs">
                  <span className="text-stone-400 text-xs">
                    Gain de caractéristique (niv. multiple de 4) — +1 à :
                  </span>
                  <select
                    className="mt-0.5 w-full bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-amber-400"
                    value={form.gainCarac}
                    onChange={(e) => set("gainCarac", e.target.value as CaracCle | "")}
                  >
                    <option value="">— Aucun —</option>
                    {CARACS.map((c) => (
                      <option key={c} value={c}>{LIBELLES_CARACS[c]}</option>
                    ))}
                  </select>
                </label>
              )}
            </section>

            {/* -------------------- Calculs automatiques -------------------- */}
            <section className="bg-stone-800/40 border border-amber-700/30 rounded-lg p-4">
              <h2 className="font-serif text-lg text-amber-200 mb-1">
                Valeurs calculées automatiquement
              </h2>
              <p className="text-xs text-stone-500 mb-3">
                Selon les règles D&amp;D 3.5 : dé de vie de la classe, ajustements raciaux,
                modificateurs de caractéristiques.
              </p>
              <div className="grid grid-cols-3 md:grid-cols-4 gap-2 text-center">
                {[
                  {
                    label:
                      calc.bonusDons > 0
                        ? `Points de vie (dons +${calc.bonusDons})`
                        : "Points de vie",
                    valeur: calc.pvMax,
                  },
                  { label: "CA", valeur: calc.ca },
                  { label: "BBA", valeur: fmtBonus(calc.bab) },
                  { label: "Initiative", valeur: fmtBonus(calc.initiative) },
                  { label: "Vigueur", valeur: fmtBonus(calc.sauves.Vigueur) },
                  { label: "Réflexes", valeur: fmtBonus(calc.sauves.Reflexes) },
                  { label: "Volonté", valeur: fmtBonus(calc.sauves.Volonte) },
                  {
                    label: `Charge max (${raceModele?.taille ?? "M"})`,
                    valeur: calc.complet ? `${calc.chargeMax} kg` : "—",
                  },
                ].map(({ label, valeur }) => (
                  <div key={label} className="bg-stone-900/60 border border-stone-700 rounded p-2">
                    <div className="text-xl text-amber-300 tabular-nums">{valeur}</div>
                    <div className="text-[11px] text-stone-500">{label}</div>
                  </div>
                ))}
              </div>
              {!calc.complet && (
                <p className="text-xs text-amber-500/80 mt-2 italic">
                  Choisissez une race et une classe pour activer tous les calculs.
                </p>
              )}
            </section>

            {/* --------------------- Avancement (niveau) -------------------- */}
            {avancement.length > 0 && (
              <section className="bg-stone-800/40 border border-emerald-700/40 rounded-lg p-4">
                <h2 className="font-serif text-lg text-amber-200 mb-1">
                  Avancement — niveau {form.niveau}
                </h2>
                <p className="text-xs text-stone-500 mb-3">
                  Gains attendus à ce niveau (PHB 3.5) — réglez chaque point
                  dans les sections correspondantes avant d'enregistrer.
                </p>
                <ul className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
                  {avancement.map((it) => (
                    <li key={it.libelle} className="flex items-start gap-2 text-xs">
                      <span
                        className={
                          it.etat === "ok"
                            ? "text-emerald-400"
                            : it.etat === "a_faire"
                              ? "text-amber-400"
                              : "text-stone-500"
                        }
                      >
                        {it.etat === "ok" ? "✓" : it.etat === "a_faire" ? "●" : "ℹ"}
                      </span>
                      <span className="leading-snug">
                        <span className="text-stone-100">{it.libelle}</span>
                        <span className="text-stone-500"> — {it.note}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* -------------------------- Capacités ------------------------ */}
            <section className="bg-stone-800/40 border border-stone-700/60 rounded-lg p-4">
              <h2 className="font-serif text-lg text-amber-200 mb-1">Capacités</h2>
              <p className="text-xs text-stone-500 mb-3">
                Traits raciaux et capacités de classe acquises au niveau choisi
                (PHB 3.5) — ils figurent aussi sur la fiche du personnage.
              </p>
              {!raceModele && !classeModele && (
                <p className="text-xs text-amber-500/80 italic">
                  Choisissez une race et une classe pour voir les capacités.
                </p>
              )}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {capacites.race.length > 0 && (
                  <div>
                    <div className="text-xs text-stone-400 mb-1.5">
                      Traits raciaux — <span className="text-amber-200">{form.race}</span>
                    </div>
                    <ul className="space-y-1.5">
                      {capacites.race.map((c) => (
                        <li
                          key={c.nom}
                          className="bg-stone-900/60 border border-stone-800 rounded p-2"
                          title={c.description}
                        >
                          <div className="text-xs text-stone-100 font-medium">{c.nom}</div>
                          <div className="text-[11px] text-stone-500 leading-snug">{c.description}</div>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {capacites.classe.length > 0 && (
                  <div>
                    <div className="text-xs text-stone-400 mb-1.5">
                      Capacités de classe —{" "}
                      <span className="text-amber-200">
                        {form.classe} niv. {form.niveau}
                      </span>
                    </div>
                    <ul className="space-y-1.5">
                      {capacites.classe.map((c) => (
                        <li
                          key={c.nom}
                          className="bg-stone-900/60 border border-stone-800 rounded p-2"
                          title={c.description}
                        >
                          <div className="text-xs text-stone-100 font-medium">
                            {c.nom}
                            {(c.niveau ?? 1) > 1 && (
                              <span className="text-amber-300/80 font-sans text-[10px] ml-1.5">
                                niv. {c.niveau}
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-stone-500 leading-snug">{c.description}</div>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </section>

            {/* -------------------- Charge transportée -------------------- */}
            <section
              className={`border rounded-lg p-4 ${
                etatCharge.depasse
                  ? "bg-rose-900/20 border-rose-500/60"
                  : etatCharge.max
                    ? "bg-amber-900/20 border-amber-700/40"
                    : "bg-stone-800/40 border-stone-700/60"
              }`}
            >
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="font-serif text-lg text-amber-200">Charge transportée</h2>
                {calc.complet && (
                  <span
                    className={`ml-auto px-2 py-0.5 rounded text-xs font-semibold ${
                      etatCharge.depasse
                        ? "bg-rose-600 text-white"
                        : etatCharge.max
                          ? "bg-amber-600 text-black"
                          : "bg-emerald-700 text-white"
                    }`}
                  >
                    {etatCharge.cat}
                  </span>
                )}
              </div>
              <p className="text-xs text-stone-500 mb-3">
                Poids des armes, armures et objets choisis selon le PHB 3.5, rapporté à la
                capacité de transport (Force × taille).
              </p>
              <div className="flex items-end justify-between mb-1">
                <span className="text-xl text-stone-100 tabular-nums">
                  {poidsPorte.poids.toFixed(2)} kg
                  <span className="text-sm text-stone-500"> / {calc.chargeMax || "—"} kg</span>
                </span>
                <span className="text-xs text-stone-500">
                  {etatCharge.pct.toFixed(0)}% de la charge max
                </span>
              </div>
              {calc.complet ? (
                <>
                  <div className="h-2.5 w-full rounded-full bg-stone-800 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${
                        etatCharge.depasse
                          ? "bg-rose-500"
                          : etatCharge.max
                            ? "bg-amber-400"
                            : etatCharge.cat === "Moyenne"
                              ? "bg-yellow-300"
                              : "bg-emerald-500"
                      }`}
                      style={{ width: `${etatCharge.pct}%` }}
                    />
                  </div>
                  <p className="text-xs mt-1 text-stone-500">
                    Légère ≤ ⅓ · Moyenne ≤ ⅔ · Lourde ≤ max · Dépassée au-delà (PHB 3.5).
                  </p>
                  {poidsPorte.inconnus.length > 0 && (
                    <p className="text-xs text-orange-500/90 mt-1">
                      Objets hors catalogue (poids non compté) :{" "}
                      {poidsPorte.inconnus.join(", ")}
                    </p>
                  )}
                  {/* 💰 Poids de l'or itemisé : sans cette ligne, la charge
                      affichait un « poids fantôme » à équipement vide (1500 pc
                      ≈ 13,6 kg — PHB : 50 pièces = 1 livre) que tout le monde
                      prenait pour un bug. */}
                  {poidsOrKg > 0 && (
                    <p className="text-xs text-stone-500 mt-1">
                      dont or : <span className="tabular-nums">{poidsOrKg.toFixed(2)} kg</span>{" "}
                      ({Number(form.or) || 0} po = {(Number(form.or) || 0) * 10} pc — 50 pc = 0,45 kg).
                    </p>
                  )}
                  {etatCharge.depasse && (
                    <p className="text-xs text-rose-300 mt-2 font-medium">
                      ⚠️ La charge dépasse la capacité maximale. Selon les règles PHB 3.5 le
                      personnage ne peut pas avancer ainsi ; la création reste possible mais
                      l'encombrement sera « Dépassée » en jeu.
                    </p>
                  )}
                </>
              ) : (
                <p className="text-xs text-amber-500/80 mt-1 italic">
                  Choisissez une race et une classe pour calculer la capacité de charge.
                </p>
              )}
            </section>

            {/* ------------------------- Apparence ------------------------- */}
            <section className="bg-stone-800/40 border border-stone-700/60 rounded-lg p-4">
              <div className="flex flex-wrap items-center gap-3 mb-1">
                <h2 className="font-serif text-lg text-amber-200">Apparence</h2>
                <button
                  type="button"
                  onClick={() => apparenceAleatoire.mutate()}
                  disabled={
                    apparenceAleatoire.isPending || !form.race || !form.classe
                  }
                  title={
                    form.race && form.classe
                      ? "Tables officielles : âge (race × classe), taille et poids (race × sexe)"
                      : "Choisissez d'abord une race et une classe"
                  }
                  className="ml-auto px-3 py-1.5 bg-violet-700 hover:bg-violet-600 disabled:opacity-40 rounded text-sm font-medium"
                >
                  🎲 Tirer âge / taille / poids
                </button>
              </div>
              {apparenceAleatoire.isSuccess && (
                <p className="text-xs text-emerald-400 mb-2">
                  Tirage officiel — âge selon la classe ({apparenceAleatoire.data.d.formule_age}).
                </p>
              )}
              <p className="text-xs text-stone-500 mb-3">
                Âge, taille et poids proviennent exclusivement du tirage aux
                tables officielles. Ces éléments alimentent la génération
                automatique du portrait.
              </p>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <label className="block">
                  <span className="text-stone-400 text-xs">Sexe</span>
                  <select
                    className="mt-0.5 w-full bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-amber-400"
                    value={form.sexe}
                    onChange={(e) => set("sexe", e.target.value)}
                  >
                    <option value="">—</option>
                    <option value="M">Masculin</option>
                    <option value="F">Féminin</option>
                    <option value="Autre">Autre</option>
                  </select>
                </label>
                {(
                  [
                    ["Âge", form.age],
                    ["Taille / corpulence", form.taille],
                    ["Poids", form.poids],
                  ] as const
                ).map(([libelle, valeur]) => (
                  <div key={libelle}>
                    <span className="text-stone-400 text-xs">{libelle}</span>
                    <div
                      className={`mt-0.5 w-full bg-stone-900 border rounded px-2.5 py-1.5 text-sm ${
                        valeur
                          ? "border-stone-700 text-amber-100"
                          : "border-dashed border-stone-600 text-stone-500 italic"
                      }`}
                      title="Tiré uniquement aux tables officielles (bouton 🎲)"
                    >
                      {valeur || "à tirer…"}
                    </div>
                  </div>
                ))}
                {champClasse("Yeux", form.yeux, (v) => set("yeux", v), "ex : verts")}
                {champClasse("Cheveux", form.cheveux, (v) => set("cheveux", v), "ex : blanc argenté")}
                {champClasse("Peau", form.peau, (v) => set("peau", v), "ex : hâlée")}
                <div className="col-span-2 md:col-span-4">
                  <label className="block">
                    <span className="text-stone-400 text-xs">
                      Traits distinctifs (cicatrice, tatouage, tenue…)
                    </span>
                    <textarea
                      rows={2}
                      className="mt-0.5 w-full bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-amber-400"
                      value={form.description}
                      onChange={(e) => set("description", e.target.value)}
                      placeholder="ex : longue tresse tressée, cape verte usée par les voyages"
                    />
                  </label>
                </div>
              </div>
            </section>

            {/* -------------- Équipement, armes, armures, or --------------- */}
            <section className="bg-stone-800/40 border border-stone-700/60 rounded-lg p-4 space-y-5">
              <div className="flex flex-wrap items-end gap-3">
                <div>
                  <span className="text-stone-400 text-xs">Or de départ (po — la fiche stocke en pc, ×10)</span>
                  <div
                    className="mt-0.5 w-36 bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm text-amber-100"
                    title="Déterminé uniquement par tirage (table PHB)"
                  >
                    {form.or ? `${form.or} po` : "à tirer…"}
                  </div>
                </div>
                <button
                  type="button"
                  disabled={!form.classe || orDepart.isPending}
                  onClick={() => orDepart.mutate({ classe: form.classe, mode: "tirage" })}
                  className="px-3 py-1.5 bg-stone-700 hover:bg-stone-600 disabled:opacity-40 border border-stone-600 rounded text-sm text-stone-200"
                >
                  🎲 Tirer
                </button>
                <span
                  className={`text-sm font-medium pb-1.5 ${
                    soldeOr < 0 ? "text-rose-400" : "text-emerald-400"
                  }`}
                >
                  Dépensé : {depenseEquipement} po · Solde : {soldeOr} po
                  {soldeOr < 0 ? " ⚠️ trop dépensé !" : ""}
                </span>
                <span className="text-xs text-stone-500 pb-1.5">
                  {form.classe && modele.data?.or_depart?.[form.classe]
                    ? `Table PHB : ${modele.data.or_depart[form.classe].des} × ${modele.data.or_depart[form.classe].mult}`
                    : "Choisissez une classe pour la formule d'or de départ."}
                </span>
              </div>

              <div>
                <h2 className="font-serif text-base text-amber-200 mb-2">
                  Armes
                  {!form.classe && (
                    <span className="text-xs text-stone-500 font-sans ml-2">
                      (toutes affichées — choisissez une classe pour griser les non-maîtrisées)
                    </span>
                  )}
                </h2>
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-x-4 gap-y-1">
                  {(modele.data?.armes ?? []).map((a) => {
                    const cochee = form.armesChoisies.includes(a.nom);
                    const dispo = armesDispo.has(a.nom);
                    const utilisable = dispo || cochee;
                    return (
                      <label
                        key={a.nom}
                        className={`flex items-center gap-2 text-xs rounded px-1 py-0.5 ${
                          utilisable ? "" : "opacity-40 cursor-not-allowed"
                        }`}
                      >
                        <input
                          type="checkbox"
                          className="accent-amber-500"
                          disabled={!utilisable}
                          checked={cochee}
                          onChange={() => toggleListe("armesChoisies", a.nom)}
                        />
                        <span>
                          {a.nom}
                          {a.distance ? " 🎯" : ""}
                          <span className="text-stone-500">
                            {" "}
                            · {a.degats} · {a.groupe === "simple" ? "simple" : a.groupe === "exotique" ? "exotique" : "martial"} ·{" "}
                            {fmtPo(a.cout)}
                            {a.poids !== undefined && ` · ${a.poids} kg`}
                          </span>
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>

              <div>
                <h2 className="font-serif text-base text-amber-200 mb-2">Armures & boucliers</h2>
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-x-4 gap-y-1">
                  {(modele.data?.armures ?? []).map((a) => {
                    const coched = form.armuresChoisies.includes(a.nom);
                    const dispo = armuresDispo.has(a.nom);
                    const usable = dispo || coched;
                    return (
                      <label
                        key={a.nom}
                        className={`flex items-center gap-2 text-xs rounded px-1 py-0.5 ${
                          usable ? "" : "opacity-40 cursor-not-allowed"
                        }`}
                      >
                        <input
                          type="checkbox"
                          className="accent-amber-500"
                          disabled={!usable}
                          checked={coched}
                          onChange={() => toggleListe("armuresChoisies", a.nom)}
                        />
                        <span>
                          {a.nom}
                          <span className="text-stone-500">
                            {" "}
                            · {a.categorie.toLowerCase()} · CA +{a.ca}
                            {a.malus > 0 ? ` · malus ${a.malus}` : ""} · {fmtPo(a.cout)}
                            {a.poids !== undefined && ` · ${a.poids} kg`}
                          </span>
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>

              <div>
                <h2 className="font-serif text-base text-amber-200 mb-2">
                  Équipement d'aventurier
                </h2>
                <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-x-4 gap-y-1">
                  {(modele.data?.equipement_aventurier ?? []).map((o) => (
                    <label key={o.nom} className="flex items-center gap-2 text-xs rounded px-1 py-0.5">
                      <input
                        type="checkbox"
                        className="accent-amber-500"
                        checked={form.equipChoisi.includes(o.nom)}
                        onChange={() => toggleListe("equipChoisi", o.nom)}
                      />
                      <span>
                        {o.nom}
                        <span className="text-stone-500">
                          {" "}
                          · {fmtPo(o.cout)}
                          {o.poids !== undefined && ` · ${o.poids} kg`}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
                <label className="block mt-3">
                  <span className="text-stone-400 text-xs">
                    Objets supplémentaires (une ligne = un objet, « Nom x2 » pour les quantités)
                  </span>
                  <textarea
                    rows={2}
                    className="mt-0.5 w-full bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-amber-400"
                    value={form.equipLibre}
                    onChange={(e) => set("equipLibre", e.target.value)}
                    placeholder="ex : Amulette familiale"
                  />
                </label>
              </div>
            </section>

            {/* --------------------------- Dons ----------------------------- */}
            <section className="bg-stone-800/40 border border-stone-700/60 rounded-lg p-4 space-y-3">
              <h2 className="font-serif text-lg text-amber-200">
                Dons
                <span
                  className={`text-xs font-sans ml-2 ${
                    donsTotal > budgetDons ? "text-rose-400" : "text-stone-500"
                  }`}
                >
                  ({donsTotal} / {budgetDons} autorisés — 1 au niv. 1 puis 1
                  tous les 3 niveaux ; humain : +1 ;
                  guerrier : +1 aux niv. 1, 2, 4, 6…)
                </span>
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-x-4 gap-y-1">
                {(modele.data?.dons ?? []).map((d) => {
                  const coche = form.donsChoisis.includes(d.nom);
                  const dispo = donDisponible(d, calc.final, calc.bab);
                  const ok = dispo || coche;
                  // Info-bulle explicite sur les dons NON sélectionnables
                  // (partie réelle : « Arme de prédilection » grisé sans
                  // aucune explication).
                  const raison = !ok
                    ? `Prérequis non remplis (${d.condition || "voir description"}).`
                    : !coche && donsPlein
                      ? `Budget de dons atteint (${donsTotal}/${budgetDons}) — décochez un don pour en choisir un autre.`
                      : undefined;
                  return (
                    <label
                      key={d.nom}
                      title={raison}
                      className={`flex items-start gap-2 text-xs rounded px-1 py-0.5 ${
                        ok && (!donsPlein || coche) ? "" : "opacity-40 cursor-not-allowed"
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="accent-amber-500 mt-0.5"
                        disabled={!ok || (!coche && donsPlein)}
                        checked={coche}
                        onChange={() => toggleListe("donsChoisis", d.nom)}
                      />
                      <span>
                        {d.nom}
                        {d.condition && (
                          <span className="text-stone-500"> · {d.condition}</span>
                        )}
                      </span>
                    </label>
                  );
                })}
              </div>
              {donsTotal > budgetDons && (
                <p className="text-rose-400 text-xs">
                  Trop de dons ({donsTotal}) pour le niveau {form.niveau} —
                  maximum : {budgetDons}. Retirez-en avant d'enregistrer.
                </p>
              )}
              <label className="block">
                <span className="text-stone-400 text-xs">
                  Dons supplémentaires (un par ligne — consomment le même budget)
                </span>
                <textarea
                  rows={2}
                  className="mt-0.5 w-full bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-amber-400"
                  value={form.donsLibre}
                  onChange={(e) => set("donsLibre", e.target.value)}
                />
              </label>
            </section>

            {/* ------------------------------ Sorts ----------------------------- */}
            {magie.lanceur && (
              <section className="bg-stone-800/40 border border-purple-700/40 rounded-lg p-4 space-y-3">
                <h2 className="font-serif text-lg text-purple-200">
                  Sorts
                  <span className="text-xs font-sans ml-2 text-stone-500">
                    {form.classe} — incantation{" "}
                    {sortsLib.typeLancement(modele.data, form.classe)} · carac.{" "}
                    {magie.cle} · niveau de sort max : {magie.nls}
                  </span>
                </h2>
                {/* Emplacements de sorts par jour (slots). */}
                <div className="flex flex-wrap gap-2">
                  {Object.entries(magie.slots).map(([lvl, total]) => (
                    <div
                      key={lvl}
                      className="bg-stone-900/60 border border-stone-700 rounded px-2 py-1 text-xs"
                    >
                      niv. {lvl} :{" "}
                      <span className="text-purple-300">{total}</span> empl./jour
                    </div>
                  ))}
                </div>
                {form.classe === "Magicien" && form.niveau === 1 && (
                  <p className="text-xs text-stone-400">
                    Grimoire de départ : tous les tours de magicien (inclus
                    ci-dessous, grisés) + {magie.budgetGrimoire} sort(s) de
                    niveau 1. La mémorisation quotidienne se fera en jeu (repos +
                    préparation avec le MJ).
                  </p>
                )}
                {form.classe === "Magicien" && form.niveau > 1 && (
                  <p className="text-xs text-stone-400">
                    Grimoire : {magie.budgetGrimoire} sorts de niveau ≥ 1 au
                    total (départ 3 + mod INT, +2 par niveau gagné — tours
                    d'office connus, grisés). La mémorisation quotidienne se
                    fera en jeu (repos + préparation avec le MJ).
                  </p>
                )}
                {(form.classe === "Sorcier" || form.classe === "Barde") && (
                  <p className="text-xs text-stone-400">
                    Choisissez vos sorts connus — budget par niveau indiqué entre
                    parenthèses.
                  </p>
                )}
                {["Clerc", "Druide", "Paladin", "Rodeur"].includes(form.classe) && (
                  <p className="text-xs text-stone-400">
                    Lanceur divin préparé : la liste complète de votre classe est
                    disponible — vous mémoriserez vos sorts du jour en jeu (repos
                    + préparation avec le MJ). Rien à choisir ici.
                  </p>
                )}
                {(form.classe === "Magicien" ||
                  form.classe === "Sorcier" ||
                  form.classe === "Barde") && (
                  <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                    {Object.entries(
                      magie.liste.reduce<Record<number, SortModele[]>>(
                        (acc, s) => {
                          (acc[s.niveau] ??= []).push(s);
                          return acc;
                        },
                        {},
                      ),
                    ).map(([lvl, sorts]) => {
                      // Budget du niveau : table PHB (Sorcier/Barde) ou
                      // grimoire de départ (Magicien niv.1 : 3 + mod INT).
                      const budget =
                        magie.budgetConnus[Number(lvl)] ??
                        (form.classe === "Magicien" && form.niveau === 1 && Number(lvl) === 1
                          ? magie.budgetGrimoire
                          : undefined);
                      const choisis = form.sortsConnus.filter(
                        (n) => sorts.some((s) => s.nom === n),
                      ).length;
                      const plein = budget !== undefined && choisis >= budget;
                      const autoMagicien =
                        form.classe === "Magicien" && Number(lvl) === 0;
                      return (
                        <div key={lvl}>
                          <div className="text-[11px] text-purple-300/80 mb-1">
                            Niveau {lvl}
                            {budget !== undefined &&
                              ` (max ${budget} connus — ${choisis} choisis${plein ? " — complet" : ""})`}
                            {autoMagicien && " (inclus au grimoire)"}
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-x-4 gap-y-1">
                            {sorts.map((s) => {
                              const coche = form.sortsConnus.includes(s.nom);
                              // Budget plein → on ne peut cocher davantage
                              // (les cases déjà cochées restent décochables).
                              const ok = !autoMagicien && (coche || !plein);
                              return (
                                <label
                                  key={s.nom}
                                  className={`flex items-start gap-2 text-xs rounded px-1 py-0.5 ${
                                    ok ? "" : "opacity-40 cursor-not-allowed"
                                  }`}
                                  title={`${s.ecole} · ${s.incantation} · portée ${s.portee} · ${s.composantes} · ${s.duree}${s.sauvegarde ? ` · sauvegarde : ${s.sauvegarde}` : ""}\n${s.description}`}
                                >
                                  <input
                                    type="checkbox"
                                    className="accent-purple-500 mt-0.5"
                                    disabled={!ok}
                                    checked={autoMagicien || coche}
                                    onChange={() =>
                                      toggleListe("sortsConnus", s.nom)
                                    }
                                  />
                                  <span>
                                    {s.nom}
                                    <span className="text-stone-500">
                                      {" "}
                                      · {s.ecole}
                                      {s.sauvegarde ? ` · ${s.sauvegarde}` : ""}
                                    </span>
                                  </span>
                                </label>
                              );
                            })}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
                {excedentSorts > 0 && (
                  <p className="text-rose-400 text-xs">
                    Trop de sorts connus ({excedentSorts}) pour {form.classe} niv.{" "}
                    {form.niveau} — décochez avant d'enregistrer.
                  </p>
                )}
              </section>
            )}

            {/* ------------------ Familier / compagnon ----------------------- */}
            {especesCompagnon && (
              <section className="bg-stone-800/40 border border-emerald-700/40 rounded-lg p-4 space-y-3">
                <h2 className="font-serif text-lg text-emerald-200">
                  {especesCompagnon.type === "familier"
                    ? "Familier"
                    : "Compagnon animal"}
                  <span className="text-xs font-sans ml-2 text-stone-500">
                    choix officiel PHB 3.5 (facultatif) — l'animal se lie au
                    personnage et grandit avec lui
                  </span>
                </h2>
                <p className="text-xs text-stone-400">
                  {especesCompagnon.type === "familier" ? (
                    <>
                      Le familier du Magicien/Sorcier transmet une faculté
                      spéciale à son maître et reçoit des pouvoirs selon le
                      niveau (armure naturelle, Intelligence, lien
                      télépathique…). En jeu, son appel exige un rituel d'une
                      journée et 100 po de composantes — le MJ le matérialise
                      via le tool <em>appeler_familier</em>.
                    </>
                  ) : (
                    <>
                      Le compagnon animal du{" "}
                      {form.classe === "Rodeur" ? "Rodeur (niv. 4+)" : "Druide"}{" "}
                      progresse avec le niveau de classe : DV supplémentaires,
                      For/Dex, tours connus, pouvoirs (Lien, Esquive
                      totale…). En jeu, le MJ le fait rejoindre le personnage
                      via le tool <em>appeler_familier</em>.
                    </>
                  )}
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-x-4 gap-y-1">
                  <label
                    className={`flex items-center gap-2 text-xs rounded px-1 py-0.5 ${
                      form.familierEspece === "" ? "" : "opacity-70"
                    }`}
                  >
                    <input
                      type="radio"
                      className="accent-emerald-500"
                      name="familier"
                      checked={form.familierEspece === ""}
                      onChange={() => set("familierEspece", "")}
                    />
                    <span className="text-stone-400 italic">
                      Aucun {especesCompagnon.type === "familier" ? "familier" : "compagnon"}
                    </span>
                  </label>
                  {especesCompagnon.liste.map((e) => {
                    const coche = form.familierEspece === e.nom;
                    const horsNorme = e.niveau_min !== undefined;
                    return (
                      <label
                        key={e.nom}
                        className={`flex items-start gap-2 text-xs rounded px-1 py-0.5 ${coche ? "" : "opacity-80"}`}
                        title={`${e.nom} — PV ${e.pv ?? "?"} · CA ${e.ca ?? "?"} · DV ${e.dv ?? "?"}${
                          horsNorme
                            ? ` · hors-norme : niveau effectif −${e.reduction}`
                            : ""
                        }${e.faculte ? `\n${e.faculte}` : ""}`}
                      >
                        <input
                          type="radio"
                          className="accent-emerald-500 mt-0.5"
                          name="familier"
                          checked={coche}
                          onChange={() => set("familierEspece", e.nom)}
                        />
                        <span>
                          {e.nom}
                          {horsNorme && (
                            <span className="text-amber-500/80">
                              {" "}
                              · hors-norme (−{e.reduction})
                            </span>
                          )}
                          <span className="text-stone-500">
                            {" "}
                            · {e.pv ?? "?"} PV · CA {e.ca ?? "?"}
                          </span>
                          {e.faculte && (
                            <span className="block text-[11px] text-stone-500">
                              {e.faculte}
                            </span>
                          )}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </section>
            )}

            {/* ----------------------- Compétences -------------------------- */}
            <section className="bg-stone-800/40 border border-stone-700/60 rounded-lg p-4 space-y-3">
              <h2 className="font-serif text-lg text-amber-200">
                Compétences
                <span
                  className={`text-xs font-sans ml-2 ${
                    rangsUtilises > budgetRangs ? "text-rose-400" : "text-stone-500"
                  }`}
                >
                  rangs utilisés : {rangsUtilises}
                  {budgetRangs > 0 && ` / budget : ${budgetRangs}`}
                  {" — "}hors classe grisées (×2 en règles complètes)
                </span>
              </h2>
              {rangsUtilises > budgetRangs && (
                <p className="text-rose-400 text-xs">
                  Trop de rangs dépensés ({rangsUtilises}) pour un budget de{" "}
                  {budgetRangs}. Réduisez avant d'enregistrer.
                </p>
              )}
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-x-4 gap-y-1">
                {(modele.data?.competences ?? []).map((c) => {
                  const active = c.nom in form.competencesRangs;
                  const deClasse = competencesDeClasse.has(c.nom);
                  const ok = deClasse || active;
                  return (
                    <label
                      key={c.nom}
                      className={`flex items-center gap-2 text-xs rounded px-1 py-0.5 ${
                        ok ? "" : "opacity-40 cursor-not-allowed"
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="accent-amber-500"
                        disabled={!ok}
                        checked={active}
                        onChange={() => toggleCompetence(c.nom)}
                      />
                      <span>{c.nom}</span>
                      <span className="text-stone-500">{c.cara}</span>
                      <input
                        type="number"
                        min={0}
                        max={form.niveau + 3}
                        disabled={!active}
                        value={form.competencesRangs[c.nom] ?? 0}
                        onChange={(e) =>
                          setRangCompetence(c.nom, parseInt(e.target.value) || 0)
                        }
                        className={`w-14 bg-stone-900 border border-stone-700 rounded px-1.5 py-0.5 text-right text-xs focus:outline-none focus:border-amber-400 disabled:opacity-40 ${
                          active ? "ml-auto" : "ml-auto invisible"
                        }`}
                      />
                    </label>
                  );
                })}
              </div>
            </section>

            {/* -------------------------- Histoire -------------------------- */}
            <section className="bg-stone-800/40 border border-stone-700/60 rounded-lg p-4">
              <h2 className="font-serif text-lg text-amber-200 mb-2">Histoire du personnage</h2>
              <textarea
                rows={3}
                className="w-full bg-stone-900 border border-stone-700 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-amber-400"
                value={form.histoire}
                onChange={(e) => set("histoire", e.target.value)}
                placeholder="Origines, motivation, secrets… (facultatif)"
              />
            </section>

            {/* ------------------------ Soumission ------------------------- */}
            {erreur && <p className="text-rose-400 text-sm">⚠️ {erreur}</p>}
            {succes && (
              <p className="text-emerald-400 text-sm">
                {modeEdition
                  ? "✅ Avancement confirmé — fiche verrouillée jusqu'au prochain passage de niveau. Portrait en cours de génération…"
                  : "✅ Fiche enregistrée — portrait en cours de génération d'après votre fiche et les traits de la race…"}
              </p>
            )}
            <div className="flex items-center gap-3 pb-6">
              <button
                type="submit"
                disabled={enregistrer.isPending || succes}
                className="px-5 py-2.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-40 rounded font-medium text-stone-900"
              >
                {enregistrer.isPending ? "Enregistrement…" : "Enregistrer le personnage"}
              </button>
              <span className="text-xs text-stone-500">
                Le portrait sera généré automatiquement après l'enregistrement.
              </span>
            </div>
          </>
        )}
      </form>
    </div>
  );
}
