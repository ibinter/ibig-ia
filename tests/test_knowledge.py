from ibig_agent.knowledge import KnowledgeBase


def test_faq_entries_are_loaded(kb_dir):
    kb = KnowledgeBase(kb_dir)
    ids = [f.id for f in kb.faq_for_pole("SOFT")]
    assert any("essai" in i for i in ids)
    assert kb.faq_for_pole("EDUFORM") == []


def test_known_facts_pass(kb_dir):
    kb = KnowledgeBase(kb_dir)
    text = ("L'abonnement coûte 15 000 FCFA par mois, écrivez à commercial@ibigsoft.com "
            "ou visitez https://ibigsoft.com/tarifs.")
    assert kb.verify_facts(text) == []


def test_invented_facts_are_caught(kb_dir):
    kb = KnowledgeBase(kb_dir)
    text = ("Profitez de -30 % : seulement 9 900 FCFA ! Appelez le +225 07 00 00 00 00, "
            "écrivez à promo@ibigsoft.com ou allez sur https://ibig-promo.com. "
            "Résultat garanti à 100 %.")
    kinds = {i.kind for i in kb.verify_facts(text)}
    assert {"prix", "telephone", "email", "url", "pourcentage",
            "formulation_interdite"} <= kinds


def test_dates_are_not_phone_numbers(kb_dir):
    kb = KnowledgeBase(kb_dir)
    assert kb.verify_facts("Session prévue le 2026-10-05.") == []


def test_context_contains_charter_and_pole(kb_dir):
    kb = KnowledgeBase(kb_dir)
    ctx = kb.context_for("SOFT")
    assert "L'excellence est notre passion" in ctx
    assert "IBIG SOFT" in ctx


def test_placeholder_faq_is_never_active(kb_dir):
    (kb_dir / "faq" / "market.md").write_text("""---
titre: FAQ Market
pole: MARKET
type: faq
---
## Comment passer commande ?
À COMPLÉTER

## Livrez-vous à Bouaké ?
Oui, en 48 h.
""", encoding="utf-8")
    kb = KnowledgeBase(kb_dir)
    active = [f.question for f in kb.faq_for_pole("MARKET")]
    assert active == ["Livrez-vous à Bouaké ?"]
    assert any(f.question == "Comment passer commande ?" for f in kb.faq_pending)


def test_placeholder_passages_are_not_searchable(kb_dir):
    kb = KnowledgeBase(kb_dir)
    for p in kb.search_passages("solutions palier gratuit durée essai catalogue", "SOFT", k=20):
        assert "COMPLÉTER" not in p.text.upper()


def test_repository_templates_are_all_inactive():
    """Les modèles livrés dans le dépôt ne déclenchent aucune réponse automatique."""
    from pathlib import Path
    kb = KnowledgeBase(Path(__file__).resolve().parents[1] / "knowledge")
    assert kb.faq == [] and len(kb.faq_pending) >= 30
    assert all(d.meta.get("statut") == "a_completer"
               for d in kb.documents if d.type == "fiche_pole")
