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
