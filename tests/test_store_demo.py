"""SQLite decision store and the reproducible demo statement."""
import re
from datetime import date
from pathlib import Path

import pytest

from subsweep.demo import END, generate_demo_rows, demo_statement_csv
from subsweep.detect import detect_recurring
from subsweep.findings import generate_findings
from subsweep.ingest import load_statement
from subsweep.store import Store

SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"


# ---------------------------------------------------------------- store
def test_dismiss_restore_and_list(tmp_path):
    s = Store(tmp_path / "x.db")
    s.dismiss("fnd_a"), s.dismiss("fnd_b", "still_using")
    assert s.dismissals() == {"fnd_a": "dismissed", "fnd_b": "still_using"}
    s.restore("fnd_a")
    assert s.dismissals() == {"fnd_b": "still_using"}
    s.restore("fnd_nope")                                                # harmless
    s.clear()
    assert s.dismissals() == {}


def test_changing_your_mind_overwrites(tmp_path):
    s = Store(tmp_path / "x.db")
    s.dismiss("fnd_a"), s.dismiss("fnd_a", "still_using")
    assert s.dismissals() == {"fnd_a": "still_using"}


def test_only_known_reasons_are_accepted(tmp_path):
    with pytest.raises(ValueError):
        Store(tmp_path / "x.db").dismiss("fnd_a", "'; DROP TABLE dismissals;--")


def test_data_survives_reopening_the_file_and_memory_store_works(tmp_path):
    Store(tmp_path / "x.db").dismiss("fnd_a")
    assert Store(tmp_path / "x.db").dismissals() == {"fnd_a": "dismissed"}
    m = Store(":memory:")
    m.dismiss("fnd_z")
    assert m.dismissals() == {"fnd_z": "dismissed"}


def test_store_creates_its_folder_and_holds_nothing_but_decisions(tmp_path):
    s = Store(tmp_path / "deep" / "dir" / "x.db")
    s.dismiss("fnd_a")
    tables = [r[0] for r in s._conn.execute("select name from sqlite_master where type='table'")]
    assert tables == ["dismissals"]                                       # no transactions or merchants are ever stored


def test_sql_injection_in_ids_is_harmless(tmp_path):
    s = Store(tmp_path / "x.db")
    s.dismiss("x'); DROP TABLE dismissals;--")
    assert list(s.dismissals()) == ["x'); DROP TABLE dismissals;--"]


# ---------------------------------------------------------------- demo statement
def test_demo_is_reproducible_and_seeded():
    assert demo_statement_csv(42) == demo_statement_csv(42)
    assert demo_statement_csv(42) != demo_statement_csv(7)
    assert (SAMPLES / "demo_statement.csv").read_text() == demo_statement_csv(42)          # the committed sample is the seed-42 output


def test_demo_shape():
    rows = generate_demo_rows()
    dates = [r[0] for r in rows]
    assert min(dates) == date(2024, 11, 1) or min(dates).year == 2024 and max(dates) <= END
    assert dates == sorted(dates) and 500 <= len(rows) <= 700
    text = demo_statement_csv()
    assert text.splitlines()[0] == "Date,Description,Amount" and all(re.match(r"\d{4}-\d\d-\d\d,\"", l) for l in text.splitlines()[1:])


def test_demo_has_no_personal_data():
    text = demo_statement_csv()
    assert "@" not in text and not re.search(r"\b\d{9,}\b", text.replace("4029357733", "").replace("800-833-6687", ""))


def test_demo_parses_as_a_bank_statement_with_no_warnings():
    r = load_statement(demo_statement_csv())
    assert r.format == "generic_bank" and len(r.transactions) == 588 and r.warnings == [] and r.date_order.startswith("month-first")


def test_demo_plants_exactly_the_findings_the_docs_promise():
    st = load_statement(demo_statement_csv())
    subs = detect_recurring(st.transactions).subscriptions
    rep = generate_findings(subs, st.transactions)
    by = {}
    for f in rep.findings:
        by.setdefault(f.type, []).append(f.merchant)
    assert by["DUPLICATE"] == ["Streamly"] and by["PRICE_HIKE"] == ["Netflix"] and by["ANNUAL_RENEWAL_SOON"] == ["Namecheap"]
    assert "Hulu" in by["POSSIBLY_FORGOTTEN"]
    assert set(by) == {"DUPLICATE", "PRICE_HIKE", "POSSIBLY_FORGOTTEN", "ANNUAL_RENEWAL_SOON"}      # the mismatch needs PayPal data


def test_demo_everyday_spending_is_never_flagged_or_detected():
    st = load_statement(demo_statement_csv())
    det = detect_recurring(st.transactions)
    names = set(det.subscriptions.merchant)
    assert len(names) == 10 and not any(w in " ".join(names).lower() for w in ("kroger", "shell", "starbucks", "uber", "amzn", "coffee", "cafe"))
    excluded = {r.rule for r in det.excluded.itertuples()}
    assert {"category:groceries", "category:fuel", "category:dining_and_delivery", "category:rideshare_and_transit", "category:retail_marketplace"} <= excluded


def test_the_planted_price_hike_is_exactly_15_49_to_17_99_from_april_2026():
    st = load_statement(demo_statement_csv())
    s = detect_recurring(st.transactions).subscriptions
    ch = s[s.merchant == "Netflix"].iloc[0].price_changes[0]
    assert (ch["old_amount"], ch["new_amount"], ch["kind"]) == (15.49, 17.99, "increase") and ch["date"].year == 2026 and ch["date"].month in (3, 4)


def test_different_seeds_keep_the_planted_structure():
    for seed in (1, 7, 99):
        st = load_statement(demo_statement_csv(seed))
        rep = generate_findings(detect_recurring(st.transactions).subscriptions, st.transactions)
        assert {"DUPLICATE", "PRICE_HIKE", "ANNUAL_RENEWAL_SOON"} <= {f.type for f in rep.findings}
