"""
Pour chaque offre non traitée : calcule un score de matching avec le
profil, génère un résumé en français, un CV adapté et une lettre de
motivation (dans la langue de l'offre), puis envoie un email quotidien
via Brevo avec les meilleures candidatures prêtes.
"""

import os
import json
import re
import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
CF_ACCOUNT_ID = os.environ["CF_ACCOUNT_ID"]
CF_API_TOKEN = os.environ["CF_API_TOKEN"]
BREVO_API_KEY = os.environ["BREVO_API_KEY"]
EMAIL_DESTINATAIRE = os.environ["EMAIL_DESTINATAIRE"]

HEADERS_SUPABASE = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

CF_MODEL_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
    f"/ai/run/@cf/meta/llama-3.1-8b-instruct"
)
HEADERS_CF = {"Authorization": f"Bearer {CF_API_TOKEN}"}

MAX_OFFRES_PAR_JOUR = 30
SCORE_MINIMUM = 40  # en dessous, l'offre n'est pas retenue comme "prête"


def recuperer_profil() -> dict:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/profil?id=eq.1&select=*",
        headers=HEADERS_SUPABASE, timeout=30,
    )
    r.raise_for_status()
    lignes = r.json()
    if not lignes:
        raise RuntimeError("Table `profil` vide — exécutez setup_profil.sql d'abord.")
    return lignes[0]


def recuperer_offres_a_traiter() -> list[dict]:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/offres?statut=eq.a_qualifier&select=*"
        f"&order=date_ajout.desc&limit={MAX_OFFRES_PAR_JOUR}",
        headers=HEADERS_SUPABASE, timeout=30,
    )
    r.raise_for_status()
    return r.json()


def extraire_json(texte: str) -> dict:
    """Le modèle peut entourer le JSON de texte ou de balises ```."""
    texte = texte.strip()
    m = re.search(r"\{.*\}", texte, re.DOTALL)
    if not m:
        raise ValueError("Pas de JSON trouvé dans la réponse du modèle.")
    return json.loads(m.group(0))


def analyser_offre(offre: dict, profil: dict) -> dict:
    prompt_systeme = (
        "You are a career assistant. You MUST answer with ONLY a valid JSON "
        "object, no extra text, no markdown fences. Fields required: "
        "score (integer 0-100, how well the candidate profile matches this "
        "job offer), langue_offre (ISO code of the offer's language, e.g. "
        "'fr' or 'en'), resume_fr (a 2-sentence summary of the offer, "
        "ALWAYS written in French regardless of the offer's language), "
        "cv_texte (a short ATS-friendly CV tailored to this offer, plain "
        "text, in the SAME language as the offer), lettre_texte (a short "
        "cover letter, max 200 words, plain text, in the SAME language as "
        "the offer, professional tone)."
    )
    prompt_utilisateur = (
        f"CANDIDATE PROFILE:\n"
        f"Name: {profil['nom']}\n"
        f"Location: {profil['ville']}\n"
        f"Languages: {profil['langues']}\n"
        f"Summary: {profil['resume']}\n"
        f"Experience: {profil['experiences']}\n"
        f"Skills: {profil['competences']}\n\n"
        f"JOB OFFER:\n"
        f"Title: {offre.get('titre')}\n"
        f"Company: {offre.get('entreprise')}\n"
        f"Description: {(offre.get('description') or '')[:1500]}\n\n"
        f"Respond with ONLY the JSON object described in the system message."
    )
    resp = requests.post(
        CF_MODEL_URL,
        headers=HEADERS_CF,
        json={"messages": [
            {"role": "system", "content": prompt_systeme},
            {"role": "user", "content": prompt_utilisateur},
        ]},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    texte_brut = data.get("result", {}).get("response", "")
    return extraire_json(texte_brut)


def mettre_a_jour_offre(offre_id: str, resultat: dict):
    score = resultat.get("score", 0)
    nouveau_statut = "pret" if score >= SCORE_MINIMUM else "rejete"
    payload = {
        "score": score,
        "langue_offre": resultat.get("langue_offre"),
        "resume_fr": resultat.get("resume_fr"),
        "cv_genere": resultat.get("cv_texte"),
        "lettre_generee": resultat.get("lettre_texte"),
        "statut": nouveau_statut,
    }
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/offres?id=eq.{offre_id}",
        headers=HEADERS_SUPABASE, json=payload, timeout=30,
    )
    if r.status_code not in (200, 204):
        print(f"Erreur mise à jour offre {offre_id}: {r.status_code} {r.text[:300]}")


def recuperer_offres_pretes_non_notifiees() -> list[dict]:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/offres?statut=eq.pret&notifie=eq.false"
        f"&select=*&order=score.desc",
        headers=HEADERS_SUPABASE, timeout=30,
    )
    r.raise_for_status()
    return r.json()


def construire_email_html(offres: list[dict]) -> str:
    blocs = []
    for o in offres:
        blocs.append(f"""
        <div style="margin-bottom:24px;padding:16px;border:1px solid #ddd;border-radius:8px;">
            <h3 style="margin:0 0 8px;">{o.get('titre','')} — {o.get('entreprise','')}</h3>
            <p><b>Score de compatibilité :</b> {o.get('score','?')}/100</p>
            <p><b>Résumé :</b> {o.get('resume_fr','')}</p>
            <p><a href="{o.get('lien','')}">Voir et postuler à l'offre</a></p>
        </div>
        """)
    return f"""
    <html><body>
    <h2>Vos {len(offres)} candidatures prêtes aujourd'hui</h2>
    {''.join(blocs)}
    <p style="color:#888;font-size:12px;">Les CV et lettres complets sont dans votre table Supabase (colonnes cv_genere / lettre_generee).</p>
    </body></html>
    """


def envoyer_email(offres: list[dict]):
    if not offres:
        print("Aucune offre prête à notifier aujourd'hui.")
        return
    payload = {
        "sender": {"name": "Pipeline Emploi", "email": EMAIL_DESTINATAIRE},
        "to": [{"email": EMAIL_DESTINATAIRE}],
        "subject": f"{len(offres)} candidatures prêtes aujourd'hui",
        "htmlContent": construire_email_html(offres),
    }
    r = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
        json=payload, timeout=30,
    )
    if r.status_code not in (200, 201):
        print(f"Erreur envoi Brevo: {r.status_code} {r.text[:300]}")
        return
    # Marquer comme notifiées
    ids = [o["id"] for o in offres]
    for oid in ids:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/offres?id=eq.{oid}",
            headers=HEADERS_SUPABASE, json={"notifie": True}, timeout=30,
        )
    print(f"Email envoyé avec {len(offres)} offres.")


def main():
    profil = recuperer_profil()
    offres = recuperer_offres_a_traiter()
    print(f"{len(offres)} offres à analyser.")

    for offre in offres:
        try:
            resultat = analyser_offre(offre, profil)
            mettre_a_jour_offre(offre["id"], resultat)
            print(f"Traitée: {offre.get('titre')} → score {resultat.get('score')}")
        except Exception as e:
            print(f"Erreur sur l'offre {offre.get('id')}: {e}")

    pretes = recuperer_offres_pretes_non_notifiees()
    envoyer_email(pretes)


if __name__ == "__main__":
    main()
