from datetime import date

from sqlalchemy import select

from ibig_agent.db import PendingAction


def complete_fiche(kb_dir, pole="soft"):
    path = kb_dir / "poles" / f"{pole}.md"
    path.write_text(path.read_text(encoding="utf-8").replace("statut: a_completer",
                                                             "statut: complet"), encoding="utf-8")


def test_incomplete_poles_are_skipped(rt, llm):
    result = rt.communication.weekly_calendar(date(2026, 10, 5))
    assert result.publications == 0
    assert result.poles_ignores["SOFT"] == "fiche pôle à compléter"
    assert "social.calendar" not in llm.calls


def test_calendar_posts_wait_for_validation(rt, llm, kb_dir):
    complete_fiche(kb_dir)
    rt.kb.reload()
    llm.calendar = {"publications": [
        {"jour": "mardi", "compte_index": 0, "sujet": "Essai gratuit",
         "texte": "Essayez nos solutions 14 jours sur https://ibigsoft.com", "brief_visuel": "…"},
        {"jour": "jeudi", "compte_index": 0, "sujet": "Promo",
         "texte": "Promo à 1 000 FCFA !", "brief_visuel": "…"},
        {"jour": "jeudi", "compte_index": 99, "sujet": "hors liste", "texte": "…",
         "brief_visuel": "…"},
    ]}
    result = rt.communication.weekly_calendar(date(2026, 10, 5))
    assert result.poles_traites == ["SOFT"] and result.publications == 2
    with rt.sessions() as s:
        posts = s.scalars(select(PendingAction)).all()
    assert all(p.status == "pending" and p.level == 2 for p in posts)
    assert posts[0].payload["date"] == "2026-10-06"
    assert posts[0].title.startswith("Mardi 06/10")  # jour en français
    assert posts[0].payload["alertes"] == []
    assert any("1 000 FCFA" in a for a in posts[1].payload["alertes"])
