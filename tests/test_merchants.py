import pytest
from subsweep.ingest.merchants import clean_merchant, is_paypal_description


@pytest.mark.parametrize("raw,clean", [
    # trailing location codes and TLDs
    ("NETFLIX.COM LOS GATOS CA", "Netflix"),
    ("Spotify P3A8E1 Stockholm SE", "Spotify"),
    ("ACH DEBIT HULU 877-8244858 CA", "Hulu"),
    ("Apple.com/bill 866-712-7753 CA", "Apple"),
    ("NETFLIX.COM MUMBAI IN REF NO 884732", "Netflix"),
    # transaction IDs and reference noise
    ("NETFLIX 08/12 REF NO 884732", "Netflix"),
    ("TXN8A7F6E5D4C ADOBE SYSTEMS", "Adobe Systems"),
    ("ADOBE *CREATIVE CLOUD 800-833-6687 CA", "Adobe Creative Cloud"),
    # UPI shapes
    ("UPI/123456789012/Spotify/spotify@okaxis/Payment", "Spotify"),
    ("UPI-SWIGGY-swiggy@ybl-123456789012-Payment", "Swiggy"),
    # card masking
    ("CARD PURCHASE ****1234 DISNEY PLUS", "Disney Plus"),
    ("POS 4532 XXXX1234 SHELL OIL 57444 HOUSTON TX", "Shell Oil"),
    # processor prefixes
    ("SQ *BLUE BOTTLE COFFEE", "Blue Bottle Coffee"),
    ("PAYPAL *SPOTIFYAB 402-935-7733", "Spotifyab"),
    ("CHECKCARD 1012 HULU 877-8244858 CA", "Hulu"),
    # URL noise after other words
    ("Google *YouTube Premium g.co/helppay#", "Google YouTube Premium"),
    ("Uber *Trip help.uber.com", "Uber Trip"),
    # company suffixes
    ("SALARY CREDIT ACME PVT LTD", "Salary Credit Acme"),
])
def test_clean_merchant(raw, clean):
    assert clean_merchant(raw) == clean


@pytest.mark.parametrize("raw", ["Office365", "1Password", "iCloud storage", "YouTube Premium", "Ideas Inc"])
def test_brand_names_are_not_mistaken_for_ids(raw):
    out = clean_merchant(raw)
    assert out.lower().startswith(raw.split()[0].lower())


def test_acronyms_keep_caps_and_mixed_case_is_preserved():
    assert clean_merchant("HBO MAX") == "HBO Max"
    assert clean_merchant("iCloud storage") == "iCloud storage"


@pytest.mark.parametrize("raw", ["", None, "nan", "   "])
def test_empty_inputs(raw):
    assert clean_merchant(raw) == ""


def test_never_returns_empty_for_all_noise():
    assert clean_merchant("1234567890") != ""


def test_paypal_prefix_detection():
    assert is_paypal_description("PAYPAL *NETFLIX")
    assert is_paypal_description("PP*SPOTIFY")
    assert not is_paypal_description("NETFLIX.COM")


@pytest.mark.parametrize("raw,clean", [
    ("Streamly Plus (Annual)", "Streamly Plus (Annual)"),     # balanced parentheses survive
    ("NETFLIX (PREMIUM)", "Netflix (Premium)"),
    ("Spotify )", "Spotify"),                                 # stray closing bracket is still trimmed
    ("- Hulu -", "Hulu"),                                     # ordinary edge punctuation is still trimmed
])
def test_balanced_parentheses_are_kept_but_stray_punctuation_is_trimmed(raw, clean):
    assert clean_merchant(raw) == clean


@pytest.mark.parametrize("raw,clean", [
    ("PAYPAL *FITPASS GYM 4029357733", "FitPass Gym"), ("PAYPAL *CLOUDBOX 4029357733", "CloudBox"), ("YOUTUBE PREMIUM", "YouTube Premium"),
    ("APPLE ICLOUD STORAGE", "Apple iCloud Storage"), ("NORDVPN.COM", "NordVPN"), ("GITHUB INC", "GitHub"),
])
def test_known_brands_keep_their_proper_capitalisation(raw, clean):
    assert clean_merchant(raw) == clean


def test_mixed_case_input_is_still_left_alone_and_unknown_names_are_title_cased():
    assert clean_merchant("Fitpass Gym") == "Fitpass Gym" and clean_merchant("ACME TOOLS") == "Acme Tools"
