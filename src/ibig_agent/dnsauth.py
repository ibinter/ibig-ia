"""Authentification du domaine d'envoi des campagnes (section 9) : SPF, DKIM et DMARC.

Lecture seule, par DNS sur HTTPS (JSON de Cloudflare) : aucun envoi, aucune dépendance en
plus. Sert au diagnostic, pour signaler un domaine mal authentifié avant la première
campagne (sinon les mails partent en courrier indésirable).
"""

from __future__ import annotations

import httpx

DOH = "https://cloudflare-dns.com/dns-query"
BREVO_SPF = "include:spf.brevo.com"
BREVO_DKIM_SELECTORS = ("brevo1", "brevo2")


def lookup(domain: str, rtype: str, client: httpx.Client) -> list[str]:
    resp = client.get(DOH, params={"name": domain, "type": rtype},
                      headers={"accept": "application/dns-json"})
    resp.raise_for_status()
    answers = resp.json().get("Answer") or []
    # Les réponses TXT arrivent entre guillemets, parfois coupées en morceaux
    return ["".join(a.get("data", "").replace('" "', "").strip('"').split('"'))
            for a in answers if a.get("type") in (5, 16)]


def check_domain(domain: str, client: httpx.Client | None = None) -> list[tuple[str, str, str]]:
    """Renvoie des triplets (contrôle, état « OK » ou « ATTENTION », détail)."""
    client = client or httpx.Client(timeout=10)
    out: list[tuple[str, str, str]] = []

    spf = [t for t in lookup(domain, "TXT", client) if t.lower().startswith("v=spf1")]
    if not spf:
        out.append(("SPF", "ATTENTION", f"aucun enregistrement SPF sur {domain}"))
    elif len(spf) > 1:
        out.append(("SPF", "ATTENTION", "plusieurs enregistrements SPF : un seul est permis"))
    elif BREVO_SPF not in spf[0]:
        out.append(("SPF", "ATTENTION", f"{BREVO_SPF} absent de « {spf[0][:120]} »"))
    else:
        out.append(("SPF", "OK", "Brevo autorisé à envoyer pour le domaine"))

    found = [s for s in BREVO_DKIM_SELECTORS
             if lookup(f"{s}._domainkey.{domain}", "CNAME", client)
             or lookup(f"{s}._domainkey.{domain}", "TXT", client)]
    out.append(("DKIM", "OK" if found else "ATTENTION",
                f"signature Brevo publiée ({', '.join(found)})" if found else
                "enregistrements brevo1._domainkey et brevo2._domainkey absents "
                "(à copier depuis Brevo → Expéditeurs et domaines)"))

    dmarc = [t for t in lookup(f"_dmarc.{domain}", "TXT", client)
             if t.lower().startswith("v=dmarc1")]
    if not dmarc:
        out.append(("DMARC", "ATTENTION", (f"aucun enregistrement _dmarc.{domain} "
                    "(commencer par « v=DMARC1; p=none; rua=mailto:… »)")))
    else:
        policy = next((p.split("=", 1)[1].strip() for p in dmarc[0].replace(" ", "").split(";")
                       if p.lower().startswith("p=")), "?")
        out.append(("DMARC", "OK" if policy.lower() != "none" else "ATTENTION",
                    f"politique p={policy}" + (" : surveillance seule, passer à quarantine "
                                               "une fois les envois stables"
                                               if policy.lower() == "none" else "")))
    return out
