"""
Scan quotidien d'offres d'emploi (Remotive, RemoteOK, Arbeitnow),
filtrage par catégorie métier, détection visa/relocation, et
insertion dans Supabase avec déduplication.
"""

import os
import re
import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

HEADERS_SUPABASE = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=ignore-duplicates",  # nécessite un index unique sur `lien`
}

# --- Catégories métier ciblées (mots-clés FR/EN, insensible à la casse) ---
CATEGORIES = {
    "customer_support": ["customer support", "call center", "call centre",
                          "téléprospection", "support client", "customer service"],
    "manutention": ["warehouse", "manutention", "picker", "packer", "logistics associate"],
    "agriculture": ["agriculture", "farm worker", "harvest", "agricultural"],
    "logistique": ["logistics", "logistique", "supply chain", "delivery driver"],
    "restauration_hotellerie": ["restaurant", "hospitality", "hôtellerie",
                                 "housekeeping", "kitchen staff", "waiter", "waitress"],
    "nettoyage": ["cleaning", "nettoyage", "janitor", "housekeeper"],
    "freelance_remote": ["freelance", "remote", "contract", "independent contractor"],
}

# --- Signaux visa / relocation (deux niveaux de confiance) ---
VISA_KEYWORDS_FORTS = [
    "visa sponsorship", "work permit sponsorship", "sponsor visa",
    "relocation package", "relocation assistance", "we sponsor",
]
VISA_KEYWORDS_FAIBLES = [
    "relocation", "international applicants", "accommodation provided",
    "housing provided", "work permit",
]


def categoriser(texte: str) -> list[str]:
    texte = texte.lower()
    trouves = []
    for cat, mots in CATEGORIES.items():
        if any(m in texte for m in mots):
            trouves.append(cat)
    return trouves


def signal_visa_relocation(texte: str) -> str:
    texte = texte.lower()
    if any(m in texte for m in VISA_KEYWORDS_FORTS):
        return "probable"
    if any(m in texte for m in VISA_KEYWORDS_FAIBLES):
        return "a_verifier"
    return "non_mentionne"


# --- Récupération des offres ---

def fetch_remotive() -> list[dict]:
    """Remotive : la recherche serveur est peu fiable, on récupère tout
    et on filtre nous-mêmes côté client."""
    r = requests.get("https://remotive.com/api/remote-jobs", timeout=30)
    r.raise_for_status()
    jobs = r.json().get("jobs", [])
    resultats = []
    for j in jobs:
        texte = f"{j.get('title','')} {j.get('description','')}"
        resultats.append({
            "titre": j.get("title"),
            "entreprise": j.get("company_name"),
            "lien": j.get("url"),
            "description": (j.get("description") or "")[:2000],
            "source": "remotive",
            "texte_complet": texte,
        })
    return resultats


def fetch_remoteok() -> list[dict]:
    """RemoteOK exige un User-Agent, sinon 403. Le 1er élément de la
    réponse est une notice légale à ignorer, pas une offre."""
    headers = {"User-Agent": "Mozilla/5.0 (job-pipeline-bot; contact: n/a)"}
    r = requests.get("https://remoteok.com/api", headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data and isinstance(data[0], dict) and "legal" in data[0]:
        data = data[1:]
    resultats = []
    for j in data:
        texte = f"{j.get('position','')} {j.get('description','')}"
        resultats.append({
            "titre": j.get("position"),
            "entreprise": j.get("company"),
            "lien": j.get("url"),
            "description": (j.get("description") or "")[:2000],
            "source": "remoteok",
            "texte_complet": texte,
        })
    return resultats


def fetch_arbeitnow() -> list[dict]:
    """Arbeitnow inclut des offres non-remote : filtrage sur le champ
    booléen `remote`."""
    r = requests.get("https://www.arbeitnow.com/api/job-board-api", timeout=30)
    r.raise_for_status()
    jobs = r.json().get("data", [])
    resultats = []
    for j in jobs:
        if not j.get("remote", False):
            continue
        texte = f"{j.get('title','')} {j.get('description','')}"
        resultats.append({
            "titre": j.get("title"),
            "entreprise": j.get("company_name"),
            "lien": j.get("url"),
            "description": (j.get("description") or "")[:2000],
            "source": "arbeitnow",
            "texte_complet": texte,
        })
    return resultats


def inserer_supabase(offres: list[dict]):
    if not offres:
        print("Aucune offre à insérer.")
        return
    payload = []
    for o in offres:
        payload.append({
            "titre": o["titre"],
            "entreprise": o["entreprise"],
            "lien": o["lien"],
            "description": o["description"],
            "source": o["source"],
            "categories": o["categories"],
            "visa_relocation_signal": o["visa_relocation_signal"],
            "statut": "a_qualifier",
        })
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/offres",
        headers=HEADERS_SUPABASE,
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        print(f"Erreur Supabase ({resp.status_code}): {resp.text[:500]}")
    else:
        print(f"{len(payload)} offres envoyées (les doublons sur `lien` sont ignorés).")


def main():
    toutes = []
    for fetch_fn in (fetch_remotive, fetch_remoteok, fetch_arbeitnow):
        try:
            toutes.extend(fetch_fn())
        except Exception as e:
            print(f"Erreur lors de la récupération ({fetch_fn.__name__}): {e}")

    print(f"{len(toutes)} offres récupérées au total (avant filtrage).")

    # Déduplication par lien (au sein de ce scan)
    vues = set()
    uniques = []
    for o in toutes:
        if o["lien"] and o["lien"] not in vues:
            vues.add(o["lien"])
            uniques.append(o)

    # Filtrage par catégorie métier + calcul du signal visa/relocation
    retenues = []
    for o in uniques:
        cats = categoriser(o["texte_complet"])
        if not cats:
            continue
        o["categories"] = cats
        o["visa_relocation_signal"] = signal_visa_relocation(o["texte_complet"])
        retenues.append(o)

    print(f"{len(retenues)} offres retenues après filtrage métier.")
    inserer_supabase(retenues)


if __name__ == "__main__":
    main()
