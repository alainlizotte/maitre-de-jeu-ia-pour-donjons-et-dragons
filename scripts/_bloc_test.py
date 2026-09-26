_re_mod.compile(
_RE_CONSIGNES_LLM_STRIP = _re_mod.compile(
    r"[ \t]*🎭\s*TA NARRATION[^\n]*"
    r"|[ \t]*_⚙️ Rotation gérée par le SERVEUR[^\n]*"
    r"|[ \t]*—\s*recopie CE bonus dans `lancer_degats`[^.\n]*\.?"
    r"|[ \t]*—?\s*Relance `lancer_degats`[^.\n]*\.?"
    r"|[ \t]*\((?:inventaire_ajouter|inventaire_consommer_munition)\)"
    r"\s*:[^.\n]*\.?"
    r"|>[ \t]*———?\s*et engage via `engager_combat`[^\n]*"
    r"|[ \t]*⚔️\s*Ennemis DU SC[ÉE]NARIO[^\n]*"
    r"|[ \t]*📝\s*Note du module[^\n]*"
    _re_mod.IGNORECASE,
)


)