from ibig_agent.config import load_org_config


def test_default_validator_fills_only_empty_poles(tmp_path):
    (tmp_path / "poles.yaml").write_text(
        "poles:\n"
        "  - {code: SOFT, nom: IBIG SOFT, valideur: awa@ibig.test}\n"
        "  - {code: EDUFORM, nom: IBIG EDUFORM}\n")
    org = load_org_config(tmp_path, valideur_defaut=" DG@IBIG.test ")
    assert org.pole("SOFT").valideur == "awa@ibig.test"
    assert org.pole("EDUFORM").valideur == "dg@ibig.test"
    assert load_org_config(tmp_path).pole("EDUFORM").valideur == ""

