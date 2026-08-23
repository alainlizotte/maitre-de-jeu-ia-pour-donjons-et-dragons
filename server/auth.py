"""Authentification simple par comptes locaux (fichier JSON + tokens HMAC).

Stockage :
- Comptes : `data/utilisateurs.json` — map nom → {sel, hash, date_creation}.
  Les mots de passe sont hachés PBKDF2-SHA256 (100k itérations, sel aléatoire).
- Secret de signature : `data/auth_secret.txt` (généré au premier démarrage,
  persisté pour que les tokens survivent aux redémarrages du serveur).

Tokens : `nom|expiration_epoch|signature_hmac` — transportés via l'en-tête
`Authorization: Bearer <token>`. Aucune dépendance externe (stdlib uniquement).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Any, Optional

# Durée de vie d'un token : 30 jours.
TOKEN_DUREE_S = 30 * 24 * 3600

_NOM_RE = re.compile(r"^[A-Za-z0-9_\-ÀÂÄÉÈÊËÎÏÔÖÙÛÜÇàâäéèêëîïôöùûüç ]{3,24}$")


def _utilisateurs_path(data_dir: str) -> str:
    return os.path.join(data_dir, "utilisateurs.json")


def _secret_path(data_dir: str) -> str:
    return os.path.join(data_dir, "auth_secret.txt")


def _charger_utilisateurs(data_dir: str) -> dict[str, Any]:
    try:
        with open(_utilisateurs_path(data_dir), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def _sauver_utilisateurs(data_dir: str, utilisateurs: dict[str, Any]) -> None:
    os.makedirs(data_dir, exist_ok=True)
    tmp = _utilisateurs_path(data_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(utilisateurs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _utilisateurs_path(data_dir))


def _secret_signature(data_dir: str) -> bytes:
    """Charge (ou crée) le secret HMAC persistant."""
    path = _secret_path(data_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            secret = f.read().strip()
            if secret:
                return secret.encode("utf-8")
    except OSError:
        pass
    secret = secrets.token_hex(32)
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(secret)
    except OSError:
        pass
    return secret.encode("utf-8")


def _hasher_mot_de_passe(mot_de_passe: str, sel: Optional[str] = None) -> tuple[str, str]:
    sel = sel or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac(
        "sha256", mot_de_passe.encode("utf-8"), sel.encode("utf-8"), 100_000
    )
    return sel, h.hex()


def nom_valide(nom: str) -> bool:
    return bool(_NOM_RE.match((nom or "").strip()))


def creer_utilisateur(data_dir: str, nom: str, mot_de_passe: str) -> tuple[bool, str]:
    """Crée un compte. Renvoie (ok, message)."""
    nom = (nom or "").strip()
    if not nom_valide(nom):
        return False, "Le nom d'utilisateur doit contenir entre 3 et 24 caractères."
    if len(mot_de_passe or "") < 4:
        return False, "Le mot de passe doit contenir au moins 4 caractères."
    utilisateurs = _charger_utilisateurs(data_dir)
    cle = nom.lower()
    if any(u.lower() == cle for u in utilisateurs):
        return False, "Ce nom d'utilisateur est déjà pris."
    sel, h = _hasher_mot_de_passe(mot_de_passe)
    utilisateurs[nom] = {
        "sel": sel,
        "hash": h,
        "date_creation": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _sauver_utilisateurs(data_dir, utilisateurs)
    return True, "Compte créé."


def verifier_identifiants(data_dir: str, nom: str, mot_de_passe: str) -> bool:
    utilisateurs = _charger_utilisateurs(data_dir)
    cle = (nom or "").strip().lower()
    compte = next(
        (v for k, v in utilisateurs.items() if k.lower() == cle), None
    )
    if not compte:
        return False
    _, h = _hasher_mot_de_passe(mot_de_passe or "", compte.get("sel", ""))
    return hmac.compare_digest(h, str(compte.get("hash", "")))


def generer_token(data_dir: str, nom: str) -> str:
    exp = int(time.time()) + TOKEN_DUREE_S
    payload = f"{nom}|{exp}"
    sig = hmac.new(_secret_signature(data_dir), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def verifier_token(data_dir: str, token: str) -> Optional[str]:
    """Renvoie le nom d'utilisateur si le token est valide, sinon None."""
    parts = (token or "").split("|")
    if len(parts) != 3:
        return None
    nom, exp_s, sig = parts
    try:
        exp = int(exp_s)
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    payload = f"{nom}|{exp_s}"
    attendu = hmac.new(
        _secret_signature(data_dir), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, attendu):
        return None
    # Le compte doit toujours exister (pas de token fantôme après suppression).
    utilisateurs = _charger_utilisateurs(data_dir)
    if not any(u.lower() == nom.lower() for u in utilisateurs):
        return None
    return nom


def utilisateur_depuis_header(data_dir: str, authorization: str) -> Optional[str]:
    """Extrait le nom d'utilisateur depuis `Authorization: Bearer <token>`."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return verifier_token(data_dir, authorization[7:].strip())
