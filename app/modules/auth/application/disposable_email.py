"""Blocklist of disposable / throwaway email domains.

Registration is the abuse funnel: per-IP rate limits are defeated by IP rotation,
and the attacker seen in the logs used yopmail. Refusing the throwaway domains they
lean on raises the cost of mass fake-account creation. This is a deterrent, not a
perfect filter — the list is curated, not exhaustive — and it matches a blocked
domain and its subdomains (e.g. ``foo.yopmail.com``).

Rejecting on the DOMAIN leaks nothing about whether an email is already registered,
so it does not weaken the register endpoint's anti-enumeration guarantee.
"""

DISPOSABLE_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        # yopmail and its many public aliases
        "yopmail.com",
        "yopmail.net",
        "yopmail.fr",
        "cool.fr.nf",
        "jetable.fr.nf",
        "nospam.ze.tc",
        "nomail.xl.cx",
        "mega.zik.dj",
        "speed.1s.fr",
        "courriel.fr.nf",
        "moncourrier.fr.nf",
        "monemail.fr.nf",
        "monmail.fr.nf",
        # other widely-used disposable providers
        "mailinator.com",
        "guerrillamail.com",
        "guerrillamail.net",
        "sharklasers.com",
        "grr.la",
        "10minutemail.com",
        "temp-mail.org",
        "tempmail.com",
        "tempmail.net",
        "throwawaymail.com",
        "getnada.com",
        "trashmail.com",
        "trashmail.net",
        "maildrop.cc",
        "dispostable.com",
        "fakeinbox.com",
        "mohmal.com",
        "mintemail.com",
        "emailondeck.com",
        "mailnesia.com",
        "spamgourmet.com",
        "mytemp.email",
        "discard.email",
        "tempinbox.com",
        "yomail.info",
        "fake-mail.net",
        "burnermail.io",
        "tmpmail.org",
        "tmpmail.net",
    }
)


def is_disposable_email(email: str) -> bool:
    """True when `email`'s domain is a known disposable provider (or a subdomain)."""
    domain = email.rsplit("@", 1)[-1].strip().lower().rstrip(".")
    if not domain:
        return False
    if domain in DISPOSABLE_EMAIL_DOMAINS:
        return True
    return any(domain.endswith("." + blocked) for blocked in DISPOSABLE_EMAIL_DOMAINS)
