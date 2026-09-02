"""Constructeur de prompt système — réplique le comportement des 3 filtres
OpenWebUI côté serveur, sans dépendance au framework.

Reproduit la logique de `Filtre_EtatPartie_INJECT.py` :
1. charger SystemPrompt (`modele_md/SystemPrompt_MaitreDuJeu_ALLEGE.md`,
   section entre les balises `## PROMPT SYSTÈME ...`);
2. sélectionner les sections dynamiques selon la `phase` courante (
   `prompts/sections/standard.md` + sections spécifiques) ;
3. construire un `=== RÉCAP DE L'ÉTAT === ... ===` cohérent avec le schéma
   persistant ;
4. (Phase 2) agréger les extraits RAG Knowledge Base en bloc dédié.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from ..config import AppConfig
from ..game.state import PartyState


# --------------------------------------------------------------------------- #
#  Mapping phase -> sections injectées (identique à l'original)
# --------------------------------------------------------------------------- #
_PHASE_SECTIONS: dict[str, list[str]] = {
    "opening":           ["standard", "ouverture"],
    "opening_complete":  ["standard"],
    "combat":            ["standard", "combats", "monstres"],
    "exploration":       ["standard", "exploration"],
    "voyage":            ["standard", "exploration"],
    "roleplay":          ["standard", "exploration"],
    "clôture":           ["standard", "cloture"],
    "cloture":           ["standard", "cloture"],
    "bilan":             ["standard", "cloture"],
}

# Directive injectée dans le récap tant que la quête est choisie mais qu'aucun
# événement d'histoire n'existe : force la scène d'ouverture narrative (décor
# du pitch) et interdit rencontre/image de monstre au premier tour.
_DEBUT_AVENTURE = (
    "⚠️ DÉBUT DE L'AVENTURE — aucun événement d'histoire enregistré. Ce "
    "tour-ci est la SCÈNE D'OUVERTURE : narre la mise en contexte complète "
    "selon le pitch de la quête ci-dessus (décor, ambiance, PNJ présents, "
    "objectif immédiat, situation de départ des héros), en 2-4 paragraphes, "
    "puis invite les joueurs à agir. INTERDIT ce tour : engager un combat, "
    "faire surgir un monstre, générer une image de monstre, lancer une "
    "rencontre."
)


def _sexe_libelle(sexe_brut: str) -> str:
    """Normalise un sexe stocké (« M », « F », « Autre », « f »…) en libellé."""
    s = (sexe_brut or "").strip().lower()
    if s.startswith("m"):
        return "Masculin"
    if s.startswith("f"):
        return "Féminin"
    if s.startswith("a"):
        return "Autre"
    return ""


def _genre_pj(data_dir: str, p: dict[str, Any]) -> str:
    """Genre d'un PJ : d'abord dans l'entrée d'état (`apparence.sexe`), sinon
    lu dans la fiche sur disque — le récapitulatif des PJ (`etat["pj"]`) ne
    transporte pas l'apparence. Retourne un libellé ou une chaîne vide."""
    app = p.get("apparence")
    sexe_brut = str(app.get("sexe") or "") if isinstance(app, dict) else ""
    if not sexe_brut:
        nom = str(p.get("nom") or "").strip()
        if nom and data_dir:
            try:
                from ..persos import charger_fiche
                fiche = charger_fiche(data_dir, nom) or {}
                app = fiche.get("apparence") or {}
                sexe_brut = str(app.get("sexe") or "")
            except Exception:                                   # noqa: BLE001
                return ""
    return _sexe_libelle(sexe_brut)


# --------------------------------------------------------------------------- #
#  Extraction du SystemPrompt
# --------------------------------------------------------------------------- #
def extract_system_prompt(raw_md: str) -> str:
    """Renvoie le contenu du SystemPrompt tel que marqué par la balise.

    Le fichier source contient une section « ## PROMPT SYSTÈME (à coller
    intégralement) » suivie du prompt, puis des notes (« Phase de jeu —
    sections dynamiques »). On garde tout entre la balise d'ouverture et la
    première ligne `## ` qui suit.
    """
    marker = "## PROMPT SYSTÈME"
    idx = raw_md.find(marker)
    if idx == -1:
        # Pas de balise : on retourne tout (fallback).
        return raw_md.strip()
    after = raw_md[idx + len(marker) :]
    # On retire la fin de ligne du marker (ex. "(à coller intégralement)")
    nl = after.find("\n")
    if nl != -1:
        after = after[nl + 1 :]
    # On s'arrête à la prochaine section `## ` (hors titres inline).
    end = after.find("\n## ")
    body = after if end == -1 else after[:end]
    return body.strip()


# --------------------------------------------------------------------------- #
#  Builder
# --------------------------------------------------------------------------- #
class PromptBuilder:
    """Construit le message système injecté à chaque appel au LLM."""

    def __init__(self, cfg: AppConfig, Registry=None):
        self.cfg = cfg
        self.prompts_dir = cfg.abs(cfg.paths.prompts_dir)
        self.sections_dir = cfg.abs(cfg.paths.sections_dir)
        self._system_prompt: Optional[str] = None
        self._system_prompt_opening: Optional[str] = None
        self._system_prompt_exploration: Optional[str] = None

    # ------------------------------------------------------------------ #
    def system_prompt(self) -> str:
        """Charge et cache le SystemPrompt (version allégée recommandée)."""
        if self._system_prompt is None:
            path = self.prompts_dir / "SystemPrompt_MaitreDuJeu_ALLEGE.md"
            if not path.is_file():
                # Fallback : l'ancienne version (346 lignes) — à éviter.
                path = self.prompts_dir / "SystemPrompt_MaitreDuJeu.md"
            if path.is_file():
                raw = path.read_text(encoding="utf-8")
                self._system_prompt = extract_system_prompt(raw)
            else:
                # Aucun prompt installé : placeholder minimal.
                self._system_prompt = (
                    "Tu es un Maître du Jeu de Donjons & Dragons 3.5, "
                    "narrateur et arbitre impartial, qui répond en français. "
                    "(SystemPrompt non trouvé — vérifier le déploiement de "
                    "server/prompts/)."
                )
        return self._system_prompt

    # ------------------------------------------------------------------ #
    def system_prompt_opening(self) -> str:
        """Variante courte (~1,5 ko) du system prompt, dédiée à la phase
        d'ouverture sans PJ créé. Maximise le signal tool-calling pour Gemma
        (le prompt complet noie l'instruction dans ~5 k tokens).
        """
        if self._system_prompt_opening is None:
            path = self.prompts_dir / "SystemPrompt_OUVERTURE_COURT.md"
            if path.is_file():
                raw = path.read_text(encoding="utf-8")
                # Le fichier commence par un titre H1 de doc, puis un bloc
                # explicatif destiné au lecteur humain — on injecte tout ce
                # qui suit la première ligne `---` séparatrice.
                sep = raw.find("\n---\n")
                body = raw[sep + len("\n---\n") :] if sep != -1 else raw
                self._system_prompt_opening = body.strip()
            else:
                # Fallback inline : on garde une version minimale même si
                # le fichier est manquant.
                self._system_prompt_opening = (
                    "Tu es le Maître du Jeu d'une partie de D&D 3.5, en "
                    "français. Pour tout jet de dés, création de perso ou "
                    "persistance d'état, tu DOIS appeler un tool via le "
                    "mécanisme `tool_calls` natif du payload. Le résultat "
                    "du tool est la seule source de vérité. INTERDIT : "
                    "écrire *(Simulation de l'appel ...)*. Ouverture : "
                    "présente-toi en une phrase, demande prénom+race+classe, "
                    "appelle etat_partie_patch puis lancer_caracteristiques"
                    "(methode=\"4d6_garder_3\"). Sois bref."
                )
        return self._system_prompt_opening

    # ------------------------------------------------------------------ #
    def system_prompt_exploration(self) -> str:
        """Variante courte dédiée aux phases post-ouverture (`opening_complete`
        ou `exploration`) où un PJ existe déjà. Le prompt complet (13,6 ko)
        noie le signal d'appel d'outil dès qu'on dépasse le tout premier tour
        — cette variante garde le régime « action immédiate » en couvrant
        fiches perso, monstres, donjon, combat, scénarios.
        """
        if self._system_prompt_exploration is None:
            path = self.prompts_dir / "SystemPrompt_EXPLORATION_COURT.md"
            if path.is_file():
                raw = path.read_text(encoding="utf-8")
                sep = raw.find("\n---\n")
                body = raw[sep + len("\n---\n") :] if sep != -1 else raw
                self._system_prompt_exploration = body.strip()
            else:
                # Fallback inline minimal.
                self._system_prompt_exploration = (
                    "Tu es le Maître du Jeu d'une partie de D&D 3.5, en "
                    "français. Le personnage du joueur est déjà créé : ne te "
                    "re-présente pas et ne rejoue pas l'intro. Pour tout jet "
                    "de dés, rencontre de monstre, exploration de donjon, "
                    "fiche perso ou persistance d'état, tu DOIS appeler un "
                    "tool via `tool_calls`. INTERDIT : simuler un appel ou "
                    "raconter un résultat de dés sans tool. Outils clés "
                    "exploration : monstre_consulter, carte_donjon_entrer / "
                    "_explorer / _get / _sortir, fiche_perso_creer / "
                    "_recuperer, demarrer_combat / tour_suivant_combat / "
                    "finir_combat. La quête est choisie dans l'interface — ne "
                    "liste jamais de scénarios. "
                    "Sois bref : 2-4 paragraphes par tour, finis par une "
                    "invitation à agir."
                )
        return self._system_prompt_exploration

    # ------------------------------------------------------------------ #
    def _load_section(self, name: str) -> str:
        path = self.sections_dir / f"{name}.md"
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def select_sections(self, phase: str) -> str:
        """Construit le bloc des sections dynamiques pour une phase donnée."""
        phase_norm = (phase or "").strip().lower()
        names = _PHASE_SECTIONS.get(phase_norm)
        if names is None:
            # défaut prudent : standard + ouverture
            names = ["standard", "ouverture"]
        blocs = [b for b in (self._load_section(n) for n in names) if b]
        return "\n\n---\n\n".join(blocs) if blocs else ""

    # ------------------------------------------------------------------ #
    def build_recap(self, etat: dict[str, Any]) -> str:
        """Construit le récapitulatif de l'état.

        En phase d'ouverture sans PJ créé, on produit un récap **minimal**
        orienté action — allège le contexte pour que le LLM déclenche les tools
        plutôt que de noyer l'instruction dans 6 ko de récap. Sinon on
        reproduit le récap complet (PJ, initiative, lieu, donjon, etc.).
        """
        if not etat or "_erreur" in etat:
            return (
                "=== ÉTAT DE LA PARTIE ===\n"
                "(Aucun état. C'est le tout premier message : déroule l'ouverture "
                "UNE seule fois selon la section dynamique « ouverture ». Persiste "
                "chaque choix via `etat_partie_patch` au fur et à mesure.)\n"
                "========================"
            )

        data_dir = str(self.cfg.abs(self.cfg.paths.data_dir))

        # État neuf en phase d'opening : récap minimal (allègement Gemma).
        phase = (etat.get("phase") or "").strip().lower()
        pj = etat.get("pj") or []
        # Si un PJ existe déjà mais qu'on est toujours en phase « opening »
        # (cas fréquent : le MJ a patché pj.0.nom etc. mais a oublié de
        # déplacer `phase` à `opening_complete`), on reste en récap minimal :
        # la party n'a pas d'initiative, pas de quête, etc. — le récap
        # complet serait surtout du bruit.
        if (phase in ("opening", "") or not phase) or (
            pj and phase in ("opening", "opening_complete", "exploration")
        ):
            meta = etat.get("meta", {}) or {}
            titre = meta.get("titre", "")
            distrib = (meta.get("distribution_faite") or False)
            pjs_sum = (
                "0 PJ créé."
                if not pj
                else f"{len(pj)} PJ créé(s) : "
                     + ", ".join(
                         f"{p.get('nom','?')} ({p.get('race','?')} "
                         f"{p.get('classe','?')}, genre "
                         f"{_genre_pj(data_dir, p) or '?'})" for p in pj
                     )
            )
            lignes = [
                "=== ÉTAT DE LA PARTIE ===" if not pj
                else "=== ÉTAT DE LA PARTIE (PJ créé, suite de l'aventure) ===",
                f"Partie : {titre or '(sans-titre)'} — phase : {phase or 'opening'} — {pjs_sum}",
                f"Distribution manuels : {'FAITE (ne PAS redistribuer)' if distrib else 'PAS ENCORE FAITE'}.",
            ]
            # Quête choisie via l'interface — le MJ doit la connaître même en
            # récap minimal, avec la directive de scène d'ouverture au besoin.
            quete_min = etat.get("quete", {}) or {}
            if (quete_min.get("titre") or "").strip():
                lignes.append(
                    f"Quête en cours : {quete_min.get('titre', '(sans titre)')} "
                    f"— {quete_min.get('pitch', '')}"
                )
                if not (etat.get("histoire") or []):
                    lignes.append(_DEBUT_AVENTURE)
            lignes += [
                "",
                "Pour progresser maintenant : appelle les outils adéquats "
                "(lancer_caracteristiques, fiche_perso_creer, etat_partie_patch, "
                "monstre_consulter, carte_donjon_entrer, carte_donjon_explorer…) "
                "— ne raconte pas simplement le résultat et NE TE RE-PRÉSENTE PAS.",
                "================================================",
            ]
            return "\n".join(lignes)

        # État riche (partie en cours) : récap complet.
        lignes = ["=== ÉTAT DE LA PARTIE (mémoire persistante) ==="]
        meta = etat.get("meta", {})
        if meta:
            lignes.append(
                f"Partie : {meta.get('titre', '(sans-titre)')} — "
                f"cadre : {meta.get('cadre', 'Côte des Épées')} — "
                f"règles : {meta.get('regles', 'D&D 3.5')}"
            )

        phase = etat.get("phase", "inconnue")
        lignes.append(f"Phase actuelle : {phase}")

        pjs = etat.get("pj", []) or []

        if phase == "combat":
            tour = etat.get("tour", 1)
            lignes.append(f"Tour de combat : {tour}")
            initiative = etat.get("initiative", []) or []
            if initiative:
                ordre = ", ".join(
                    f"{e.get('nom','?')} ({e.get('init','?')})" for e in initiative
                )
                lignes.append(f"Ordre d'initiative : {ordre}")
            courant = etat.get("courant_tour_pour")
            if courant:
                pj_courant = next(
                    (p for p in pjs if p.get("nom") == courant), None
                ) if pjs else None
                qui = (
                    f"{courant} (joueur : {pj_courant.get('joueur')})"
                    if pj_courant is not None
                    else f"{courant} (PNJ/monstre)"
                )
                lignes.append(f"C'est au tour de : {qui}")
                lignes.append(
                    "Résous UNIQUEMENT les actions déclarées par CE joueur. "
                    "La rotation des tours, les monstres, la clôture et l'XP "
                    "sont gérées automatiquement par le serveur — n'appelle "
                    "ni tour_suivant_combat ni finir_combat."
                )
        # Mémoire de campagne (missions, lieux, PNJ, combats, position) :
        # injectée automatiquement — le MJ la CONNAÎT sans tool de lecture.
        try:
            from ..tools.memoire import memoire_resume
            bloc_memoire = memoire_resume(etat)
            if bloc_memoire:
                lignes.append("\n=== MÉMOIRE DE CAMPAGNE ===\n" + bloc_memoire)
        except Exception:                                       # noqa: BLE001
            pass
        voyage = etat.get("voyage") or {}
        if voyage:
            lignes.append(
                f"\nVoyage en cours : {voyage.get('resume', '?')} "
                f"(jours avec rencontre : "
                f"{', '.join(str(j) for j in voyage.get('jours_rencontres', []) or []) or 'aucun'})"
            )

        pjs = etat.get("pj", []) or []
        if pjs:
            lignes.append("\nPersonnages Joueurs (nom — genre — joueur qui le joue) :")
            for p in pjs:
                lignes.append(
                    f"  - {p.get('nom','?')}: {p.get('race','?')} "
                    f"{p.get('classe','?')} niv.{p.get('niveau','?')} — "
                    f"genre {_genre_pj(data_dir, p) or '?'} — "
                    f"PV {p.get('pv','?')}/{p.get('pv_max','?')} — "
                    f"CA {p.get('ca','?')} — joueur: {p.get('joueur','?')} — "
                    f"conditions: {p.get('conditions') or 'aucune'}"
                )

        pnjs = etat.get("pnj", []) or []
        if pnjs:
            lignes.append("\nPNJ notables :")
            for p in pnjs:
                lignes.append(f"  - {p.get('nom','?')}: {p.get('role','?')}")

        lieu = etat.get("lieu", {}) or {}
        if lieu:
            lignes.append(
                f"\nLieu actuel : {lieu.get('nom', '?')} "
                f"(type: {lieu.get('type', '?')})"
            )
            if lieu.get("description"):
                lignes.append(f"  Description : {lieu['description']}")

        donjon = etat.get("donjon", {}) or {}
        if donjon.get("id"):
            salles = donjon.get("salles_visitees", []) or []
            lignes.append(
                f"\nDonjon '{donjon.get('id')}' — "
                f"{len(salles)} salle(s) visitée(s) : {', '.join(salles)}"
            )

        donjons_archives = etat.get("donjons_exploreres") or {}
        if donjons_archives:
            noms = [
                f"{did} ({len(d.get('salles_visitees', []))} salles)"
                for did, d in donjons_archives.items()
                if did and d.get("grille")
            ]
            if noms:
                lignes.append(
                    "\nDonjons déjà explorés dans cette partie (archivés) : "
                    + ", ".join(noms)
                )

        quete = etat.get("quete", {}) or {}
        if quete:
            lignes.append(
                f"\nQuête en cours : {quete.get('titre','(sans titre)')} "
                f"— {quete.get('pitch','')}"
            )
            # Début d'aventure : quête choisie mais aucun événement d'histoire
            # → on force la mise en contexte narrative AVANT toute action.
            if (quete.get("titre") or "").strip() and not (
                etat.get("histoire") or []
            ):
                lignes.append(_DEBUT_AVENTURE)

        derniere = etat.get("derniere_narration", "")
        if derniere:
            lignes.append("\nDernier événement marquant :")
            lignes.append(derniere[:1500])

        recap = "\n".join(lignes)
        if len(recap) > self.cfg.game.max_recap_chars:
            recap = recap[: self.cfg.game.max_recap_chars - 60] + "\n…[récap tronqué]"
        recap += "\n==============================="
        return recap

    # ------------------------------------------------------------------ #
    def build_system_message(
        self,
        partie_id: str,
        rag_context: str = "",
    ) -> tuple[str, dict[str, Any]]:
        """Construit le message système complet d'un tour.

        Renvoie `(system_message, etat)` — l'état est retourné pour que la
        session puisse l'exposer au frontend (UI) en parallèle du LLM.
        """
        state = PartyState(
            data_dir=str(self.cfg.abs(self.cfg.paths.data_dir)),
            partie_id=partie_id,
            max_history=self.cfg.game.max_history_events,
        )
        etat = state.load()
        recap = self.build_recap(etat)

        # Détection du régime « ouverture court » : phase opening et aucun PJ.
        # Dans ce cas, on remplace le system prompt complet (~5 k tokens) par
        # la variante courte (~1,5 k tokens) et on n'injecte QUE la section
        # `ouverture` (on zappe `standard`, 2,6 ko de règles génériques peu
        # utiles tant que le personnage n'existe pas). Objectif : laisser le
        # signal d'appel d'outil dominer pour Gemma.
        phase_raw = (etat.get("phase") if etat else "") or ""
        phase = phase_raw.strip().lower() or "opening"
        pj = (etat.get("pj") if etat else []) or []
        opening_court = phase in ("opening", "") and not pj
        # Régime « exploration court » : dès qu'un PJ existe (la phase exacte
        # importe peu — Gemma oublie souvent de déplacer `phase` de
        # `opening` à `opening_complete`, mais la présence d'un PJ est
        # un signal fort qu'on a fini la phase d'ouverture). Le prompt
        # complet noie les appels d'outil dès qu'on dépasse le premier
        # tour — on garde donc la version courte tant qu'un PJ existe et
        # qu'on n'est pas en combat (combat a son propre régime détaillé).
        exploration_court = bool(pj) and phase not in ("combat",)

        if opening_court or exploration_court:
            # Régime « court » : on n'injecte PAS la section dynamique —
            # l'énoncé des étapes est déjà inline dans le prompt court. On
            # garde aussi un récap léger (selon build_recap). Objectif :
            # garder le système aussi léger que le test diagnostic qui a fait
            # déclencher Gemma (~1-2 k tokens, schemas JSON portés par le
            # payload natif).
            sections = ""
        else:
            sections = self.select_sections(phase)

        if opening_court:
            sys_prompt = self.system_prompt_opening()
        elif exploration_court:
            sys_prompt = self.system_prompt_exploration()
        else:
            sys_prompt = self.system_prompt()
        parts = [sys_prompt, recap]
        if sections:
            parts.append(sections)
        if rag_context:
            parts.append(
                "=== CONTEXTE RÈGLES (Knowledge Base D&D 3.5) ===\n"
                + rag_context
                + "\n==============================\n"
                + "Utilise ces extraits des manuels pour appliquer fidèlement les "
                "règles quand un point mécanique se présente."
            )
        return "\n\n".join(p for p in parts if p), etat
