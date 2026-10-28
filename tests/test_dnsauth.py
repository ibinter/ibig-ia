"""Vérification SPF / DKIM / DMARC du domaine d'envoi (section 9), DNS simulé."""

import httpx

from ibig_agent.dnsauth import check_domain


def dns(records: dict[tuple[str, str], list[str]]):
    codes = {"TXT": 16, "CNAME": 5}

    def handler(request: httpx.Request) -> httpx.Response:
        name, rtype = request.url.params["name"], request.url.params["type"]
        data = records.get((name, rtype), [])
        return httpx.Response(200, json={"Answer": [
            {"name": name, "type": codes[rtype], "data": d} for d in data]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def states(client, domain="ibig.test"):
    return {name: (status, detail) for name, status, detail in check_domain(domain, client)}


def test_fully_authenticated_domain_is_ok():
    res = states(dns({
        ("ibig.test", "TXT"): ['"v=spf1 include:spf.brevo.com ~all"'],
        ("brevo1._domainkey.ibig.test", "CNAME"): ["b1.ibig-test.dkim.brevo.com."],
        ("_dmarc.ibig.test", "TXT"): ['"v=DMARC1; p=quarantine; rua=mailto:d@ibig.test"'],
    }))
    assert [s for s, _ in res.values()] == ["OK", "OK", "OK"]
    assert "brevo1" in res["DKIM"][1] and "quarantine" in res["DMARC"][1]


def test_missing_records_are_reported_with_the_fix():
    res = states(dns({}))
    assert all(s == "ATTENTION" for s, _ in res.values())
    assert "brevo1._domainkey" in res["DKIM"][1] and "v=DMARC1" in res["DMARC"][1]


def test_spf_without_brevo_and_dmarc_none_are_warnings():
    res = states(dns({
        ("ibig.test", "TXT"): ['"v=spf1 include:_spf.google.com ~all"'],
        ("brevo2._domainkey.ibig.test", "TXT"): ['"k=rsa; p=MIGf"'],
        ("_dmarc.ibig.test", "TXT"): ['"v=DMARC1; p=none"'],
    }))
    assert res["SPF"][0] == "ATTENTION" and "spf.brevo.com" in res["SPF"][1]
    assert res["DKIM"][0] == "OK"
    assert res["DMARC"][0] == "ATTENTION" and "p=none" in res["DMARC"][1]


def test_two_spf_records_are_flagged():
    res = states(dns({("ibig.test", "TXT"): ['"v=spf1 -all"', '"v=spf1 include:spf.brevo.com"']}))
    assert res["SPF"][0] == "ATTENTION" and "un seul" in res["SPF"][1]
