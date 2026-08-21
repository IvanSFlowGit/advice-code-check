#!/usr/bin/env python3
"""Tests for advice_code_check. No dependencies. Run: python3 test_advice_code_check.py

Every fixture here is SYNTHETIC. There is no real card, customer or charge in
this file, and there never should be.

The point of these is not that they pass. A suite that has only ever passed has
been run, not tested. Each case below is paired with the mutation it would catch:
if the contradiction detector stops looking forward in time, case 3 goes red; if
succeeded charges stop being excluded, case 1's population moves; if the money
counter stops keying on the refused attempt, case 4 changes.
"""
import csv, io, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import advice_code_check as m

FIELDS = ["id", "card_fingerprint", "advice_code", "created", "status", "amount", "currency"]

def rows_to_csv(rows):
    fd, p = tempfile.mkstemp(suffix=".csv"); os.close(fd)
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    return p

def r(fp, advice, ts, amount=1000, status="failed", cur="eur"):
    return {"id": f"ch_{ts}_{fp}", "card_fingerprint": fp, "advice_code": advice,
            "created": ts, "status": status, "amount": amount, "currency": cur}

def analyse_rows(rows):
    recs = [{"advice": x["advice_code"], "key": x["card_fingerprint"],
             "ts": float(x["created"]), "amount": x["amount"], "currency": x["currency"]}
            for x in rows if x["status"] != "succeeded"]
    return m.analyse(recs)

def abandoned_of(rows):
    res = analyse_rows(rows)
    return res.tal, res.abandoned, res.ab_keys, res.ab_money

class TestAnalysis(unittest.TestCase):

    def test_1_succeeded_charges_are_not_in_the_population(self):
        rows = [r("A", "do_not_try_again", 1), r("B", "", 2, status="succeeded")]
        res = analyse_rows(rows)
        self.assertEqual(sum(res.by_advice.values()), 1, "a succeeded charge must not be counted as a decline")

    def test_2_do_not_try_again_with_no_later_attempt_is_compliant(self):
        rows = [r("A", "do_not_try_again", 1)]
        res = analyse_rows(rows)
        self.assertEqual((res.dnt, res.contradicted, res.keys), (1, 0, 0))
        self.assertEqual(dict(res.money), {}, "nothing contradicted means no money attributed")

    def test_3_a_later_attempt_on_the_same_card_is_a_contradiction(self):
        rows = [r("A", "do_not_try_again", 1), r("A", "", 2)]
        res = analyse_rows(rows)
        self.assertEqual((res.dnt, res.contradicted, res.keys), (1, 1, 1))

    def test_3b_an_EARLIER_attempt_is_not_a_contradiction(self):
        """Direction matters. If the detector stops comparing timestamps this goes red."""
        rows = [r("A", "", 1), r("A", "do_not_try_again", 2)]
        self.assertEqual(analyse_rows(rows).contradicted, 0, "an attempt BEFORE the refusal cannot have been caused by it")

    def test_3c_a_later_attempt_on_a_DIFFERENT_card_is_not_a_contradiction(self):
        rows = [r("A", "do_not_try_again", 1), r("B", "", 2)]
        res = analyse_rows(rows)
        self.assertEqual((res.contradicted, res.keys), (0, 0))

    def test_4_money_is_the_amount_of_the_refused_attempt_not_the_retry(self):
        rows = [r("A", "do_not_try_again", 1, amount=4900), r("A", "", 2, amount=999)]
        self.assertEqual(dict(analyse_rows(rows).money), {"eur": 4900})

    def test_4b_money_sums_per_currency(self):
        rows = [r("A", "do_not_try_again", 1, amount=100, cur="eur"), r("A", "", 2),
                r("B", "do_not_try_again", 1, amount=250, cur="usd"), r("B", "", 2)]
        self.assertEqual(dict(analyse_rows(rows).money), {"eur": 100, "usd": 250})

    def test_5_one_card_counted_once_however_many_retries(self):
        rows = [r("A", "do_not_try_again", 1), r("A", "", 2), r("A", "", 3)]
        res = analyse_rows(rows)
        self.assertEqual((res.contradicted, res.keys), (1, 1), "one refused charge, one contradiction, one card")

class TestAbandonedRetries(unittest.TestCase):
    """try_again_later that nobody went back for. The direction that finds money."""

    def test_6_never_retried_is_abandoned(self):
        rows = [r("A", "try_again_later", 1, amount=2500)]
        tal, ab, keys, money = abandoned_of(rows)
        self.assertEqual((tal, ab, keys), (1, 1, 1))
        self.assertEqual(dict(money), {"eur": 2500})

    def test_6b_retried_is_NOT_abandoned(self):
        """If the forward-looking check breaks, this goes red rather than silently
        reporting every charge as abandoned."""
        rows = [r("A", "try_again_later", 1, amount=2500), r("A", "", 2)]
        tal, ab, keys, money = abandoned_of(rows)
        self.assertEqual((tal, ab, keys), (1, 0, 0))
        self.assertEqual(dict(money), {})

    def test_6c_a_retry_on_a_different_card_does_not_count_as_going_back(self):
        rows = [r("A", "try_again_later", 1), r("B", "", 2)]
        tal, ab, keys, _ = abandoned_of(rows)
        self.assertEqual((tal, ab, keys), (1, 1, 1))

    def test_6d_do_not_try_again_is_not_counted_as_abandoned(self):
        """The two directions must not contaminate each other."""
        rows = [r("A", "do_not_try_again", 1)]
        tal, ab, keys, _ = abandoned_of(rows)
        self.assertEqual((tal, ab, keys), (0, 0, 0))

    def test_6e_both_directions_coexist_in_one_population(self):
        rows = [r("A", "do_not_try_again", 1, amount=100), r("A", "", 2),
                r("B", "try_again_later", 1, amount=900)]
        res = analyse_rows(rows)
        self.assertEqual((res.dnt, res.contradicted), (1, 1))
        self.assertEqual((res.tal, res.abandoned), (1, 1))
        self.assertEqual(dict(res.money), {"eur": 100})
        self.assertEqual(dict(res.ab_money), {"eur": 900})

class TestRefusals(unittest.TestCase):
    def run_cli(self, *args):
        p = subprocess.run([sys.executable, os.path.join(HERE, "advice_code_check.py"), *args],
                           capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def test_missing_file_refuses(self):
        code, out = self.run_cli("--csv", "/nonexistent/nope.csv")
        self.assertEqual(code, 2); self.assertIn("REFUSED", out)

    def test_header_only_refuses_rather_than_reporting_zero(self):
        p = rows_to_csv([])
        code, out = self.run_cli("--csv", p); os.unlink(p)
        self.assertEqual(code, 3, "an empty population is a broken run, not a clean result")
        self.assertIn("nothing to measure", out)

    def test_missing_columns_refuses_and_names_them(self):
        fd, p = tempfile.mkstemp(suffix=".csv"); os.close(fd)
        with open(p, "w") as f: f.write("id,amount\nch_1,100\n")
        code, out = self.run_cli("--csv", p); os.unlink(p)
        self.assertEqual(code, 3)
        self.assertIn("advice code", out)
        self.assertIn("columns found", out, "a refusal must show what it did see")

    def test_api_without_a_key_refuses(self):
        env = dict(os.environ); env.pop("STRIPE_API_KEY", None)
        p = subprocess.run([sys.executable, os.path.join(HERE, "advice_code_check.py"), "--api"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 2)

class TestOutputHonesty(unittest.TestCase):
    """The hedges are load-bearing. If they can be dropped without a test going red,
    somebody will drop them."""
    def test_ceiling_and_not_revenue_are_stated_in_the_output(self):
        rows = [r("A", "do_not_try_again", 1, amount=4900), r("A", "", 2)]
        p = rows_to_csv(rows)
        out = subprocess.run([sys.executable, os.path.join(HERE, "advice_code_check.py"), "--csv", p],
                             capture_output=True, text=True).stdout
        os.unlink(p)
        self.assertIn("CEILING, NOT A COUNT", out)
        self.assertIn("NOT RECOVERABLE REVENUE", out)
        self.assertIn("Nothing left this machine", out)

    def test_the_abandoned_side_is_hedged_too(self):
        rows = [r("A", "try_again_later", 1, amount=2500)]
        p = rows_to_csv(rows)
        out = subprocess.run([sys.executable, os.path.join(HERE, "advice_code_check.py"), "--csv", p],
                             capture_output=True, text=True).stdout
        os.unlink(p)
        self.assertIn("THIS IS ALSO A CEILING", out)
        self.assertIn("cancelled, refunded, paid by", out)

if __name__ == "__main__":
    unittest.main(verbosity=2)
