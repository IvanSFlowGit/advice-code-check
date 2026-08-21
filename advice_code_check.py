#!/usr/bin/env python3
"""Five-minute check: how often does your retry logic contradict the issuer?

WHY THIS EXISTS. Stripe returns an issuer instruction on every declined charge at
`outcome.advice_code`: do_not_try_again, try_again_later, confirm_card_data. Most dunning
systems schedule retries off a hand-built map of decline CODES instead. A code is a claim
about a code; the advice code is the issuer's instruction about THIS charge, and the same
code carries different advice on different transactions. So a retry can contradict an
instruction the issuer already gave.

This script counts how often that happened in YOUR data, and then it counts the other
direction, which is usually the expensive one: charges the issuer said TRY AGAIN LATER
that were never attempted again. The first is wasted effort. The second is revenue nobody
went back for, on an instruction that was already there and free. It runs on YOUR machine, against
YOUR export or YOUR read-only key. Nothing is sent anywhere. There is no network call
except to Stripe's own API when you pass --api, and no write of any kind, ever.

    python3 advice_code_check.py --csv declines.csv
    STRIPE_API_KEY=rk_live_... python3 advice_code_check.py --api --days 90

CSV MODE wants a Stripe charges export. It needs, at minimum, a column carrying the
advice code and a column carrying the card fingerprint or customer id. It tells you
exactly which columns it found and which it needed, and REFUSES rather than guessing.

WHAT A RETRY MEANS HERE: a later declined or succeeded charge against the SAME card
fingerprint (or the same customer, if fingerprint is absent) after a charge whose
advice_code said do_not_try_again. That is a proxy and it is stated as one: a genuine new
purchase attempt by the customer looks the same from the outside. The number is a CEILING
on contradicting retries, never a count, and the script says so on every run.
"""
import argparse, csv, os, sys, json, collections, datetime

Result = collections.namedtuple("Result", "total by_advice dnt contradicted keys money tal abandoned ab_keys ab_money")

ADVICE = ("do_not_try_again", "try_again_later", "confirm_card_data")

def die(msg, code=2):
    print(f"REFUSED: {msg}", file=sys.stderr); sys.exit(code)

def pick(fieldnames, *candidates):
    low = {f.lower().strip(): f for f in fieldnames}
    for c in candidates:
        if c in low: return low[c]
    return None

def load_csv(path):
    if not os.path.exists(path): die(f"{path} not found")
    rows = list(csv.DictReader(open(path, encoding="utf-8", errors="replace")))
    if not rows: die(f"{path} has a header and no rows, so there is nothing to measure", 3)
    fn = rows[0].keys()
    adv = pick(fn, "advice_code", "outcome.advice_code", "outcome_advice_code",
                   "outcome[advice_code]", "network_advice_code", "outcome.network_advice_code")
    key = pick(fn, "card_fingerprint", "fingerprint", "payment_method_fingerprint",
                   "card.fingerprint", "customer_id", "customer", "customer id")
    ts  = pick(fn, "created", "created_utc", "created_at", "date", "charge_date", "created (utc)")
    st  = pick(fn, "status", "outcome.type", "outcome_type", "captured")
    amt = pick(fn, "amount", "amount_captured", "converted_amount", "amount (converted)")
    cur = pick(fn, "currency", "converted_currency", "presentment currency")
    missing = [n for n, v in (("advice code", adv), ("card fingerprint or customer", key), ("created timestamp", ts)) if not v]
    if missing:
        print(f"  columns found: {sorted(fn)}", file=sys.stderr)
        die("this export is missing: " + ", ".join(missing) + ".\n"
            "\n  THE DASHBOARD EXPORT USUALLY WILL NOT CARRY THE ADVICE CODE. That is not your"
            "\n  fault and it is the normal case. Use the API instead, which returns it directly:"
            "\n"
            "\n    1. Stripe dashboard, Developers, API keys, Create restricted key"
            "\n    2. Give it READ on Charges. Nothing else. Leave every other permission at None."
            "\n    3. STRIPE_API_KEY=rk_... python3 advice_code_check.py --api --days 90"
            "\n"
            "\n  A restricted read key cannot write, refund, charge or move money. This script"
            "\n  makes no write call in any mode.", 3)
    return rows, adv, key, ts, st, amt, cur

def analyse(records):
    """records: advice, key, ts, amount (minor units or None), currency. Returns the report."""
    by_advice = collections.Counter(r["advice"] or "(none returned)" for r in records)
    dnt = [r for r in records if r["advice"] == "do_not_try_again"]
    later = collections.defaultdict(list)
    for r in records:
        later[r["key"]].append(r)
    contradicted, affected_keys, money = 0, set(), collections.Counter()
    for r in dnt:
        for s in later[r["key"]]:
            if s is r: continue
            if s["ts"] is not None and r["ts"] is not None and s["ts"] > r["ts"]:
                contradicted += 1; affected_keys.add(r["key"])
                if r.get("amount") is not None:
                    money[(r.get("currency") or "?").lower()] += r["amount"]
                break

    # THE OTHER DIRECTION, AND IT IS THE ONE PEOPLE DO NOT LOOK FOR.
    # try_again_later is the issuer saying this may work on another day. A charge
    # carrying it that was NEVER attempted again is a recovery nobody attempted.
    tal = [r for r in records if r["advice"] == "try_again_later"]
    abandoned, abandoned_keys, abandoned_money = 0, set(), collections.Counter()
    for r in tal:
        if not any(s is not r and s["ts"] is not None and r["ts"] is not None and s["ts"] > r["ts"]
                   for s in later[r["key"]]):
            abandoned += 1; abandoned_keys.add(r["key"])
            if r.get("amount") is not None:
                abandoned_money[(r.get("currency") or "?").lower()] += r["amount"]
    return Result(len(records), by_advice, len(dnt), contradicted, len(affected_keys), money,
                  len(tal), abandoned, len(abandoned_keys), abandoned_money)

def report(res, population):
    total, by_advice, dnt = res.total, res.by_advice, res.dnt
    contradicted, keys, money = res.contradicted, res.keys, res.money
    tal, abandoned, ab_keys, ab_money = res.tal, res.abandoned, res.ab_keys, res.ab_money
    print()
    print(f"  POPULATION: {population}")
    print(f"  declined charges examined: {total}")
    if total == 0:
        die("zero charges in the population. That is a broken run, not a clean result.", 3)
    print()
    print("  issuer instruction, as returned by Stripe:")
    for a in list(ADVICE) + [k for k in by_advice if k not in ADVICE]:
        if a in by_advice:
            n = by_advice[a]
            print(f"    {a:<20} {n:>6}  {n*100.0/total:5.1f}%")
    print()
    if not dnt:
        print("  No charge in this population carried do_not_try_again, so there is nothing")
        print("  here for a retry to contradict. That is a real result and a good one.")
    else:
        _contradiction_section(dnt, contradicted, keys, money)
    _abandoned_section(tal, abandoned, ab_keys, ab_money)

def _contradiction_section(dnt, contradicted, keys, money):
    print(f"  charges the issuer said DO NOT TRY AGAIN:        {dnt}")
    print(f"  of those, a later attempt hit the same card:     {contradicted}")
    print(f"  distinct cards involved:                         {keys}")
    print()
    print(f"  => {contradicted*100.0/dnt:.1f}% of do_not_try_again charges were followed by")
    print( "     another attempt on the same card.")
    print()
    if money:
        print("  value sitting on those charges, at the amount of the refused attempt:")
        for c, v in sorted(money.items()):
            print(f"    {v/100.0:>12,.2f} {c.upper()}")
        print()
        print("  THAT IS NOT RECOVERABLE REVENUE AND MUST NOT BE READ AS ONE. It is the value")
        print("  attached to charges the issuer had already refused for good, so it is the size")
        print("  of the population being retried rather than money on the table. Some of it is")
        print("  recoverable by a NEW card, none of it by the same one.")
        print()
    else:
        print("  No amount column was available, so this run counts charges and not money.")
        print()
    print("  THIS IS A CEILING, NOT A COUNT. A later attempt on the same card looks identical")
    print("  from the outside whether your dunning system scheduled it or the customer tried")
    print("  again themselves. Treat it as the largest the problem could be, then check a")
    print("  handful by eye against your own retry log to find where it actually sits.")

def _abandoned_section(tal, abandoned, ab_keys, ab_money):
    print()
    print("  " + "-" * 68)
    print()
    print("  THE OTHER DIRECTION, WHICH IS USUALLY THE EXPENSIVE ONE.")
    print()
    if not tal:
        print("  No charge here carried try_again_later, so there is nothing that was refused")
        print("  for timing and left alone.")
        return
    print(f"  charges the issuer said TRY AGAIN LATER:          {tal}")
    print(f"  of those, NEVER attempted again on that card:     {abandoned}")
    print(f"  distinct cards involved:                          {ab_keys}")
    print()
    print(f"  => {abandoned*100.0/tal:.1f}% of try_again_later charges were never retried.")
    print()
    if ab_money:
        print("  value on charges the issuer invited you to retry and nobody did:")
        for c, v in sorted(ab_money.items()):
            print(f"    {v/100.0:>12,.2f} {c.upper()}")
        print()
    print("  THIS IS ALSO A CEILING. Some of these customers cancelled, refunded, paid by")
    print("  another route, or were retried outside the window this export covers. What it")
    print("  measures is the population your dunning did not come back to, on charges where")
    print("  the issuer said coming back was worth trying. That is the cheapest place to")
    print("  look for revenue, because the instruction was already there and free.")

def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="a Stripe charges export carrying outcome.advice_code")
    src.add_argument("--api", action="store_true", help="read from Stripe with STRIPE_API_KEY (read-only calls)")
    ap.add_argument("--days", type=int, default=90, help="lookback for --api, default 90")
    a = ap.parse_args()

    if a.csv:
        rows, adv, key, ts, st, amt, cur = load_csv(a.csv)
        recs = []
        for r in rows:
            if st and r.get(st, "").lower() in ("succeeded", "paid"): continue
            t = r.get(ts, "")
            try: tv = float(t)
            except ValueError:
                try: tv = datetime.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
                except Exception: tv = None
            av = None
            if amt:
                try: av = int(round(float(str(r.get(amt, "")).replace(",", "")) ))
                except ValueError: av = None
            recs.append({"advice": (r.get(adv) or "").strip(), "key": (r.get(key) or "").strip(),
                         "ts": tv, "amount": av, "currency": (r.get(cur) or "") if cur else ""})
        pop = f"{a.csv}, column {adv!r} for the advice code, {key!r} as the card key"
    else:
        k = os.environ.get("STRIPE_API_KEY", "")
        if not k: die("--api needs STRIPE_API_KEY in the environment. Use a RESTRICTED READ-ONLY key.")
        if k.startswith("sk_"):
            print("  WARNING: that looks like a full secret key. A restricted read-only key (rk_)", file=sys.stderr)
            print("           is enough for this and cannot write anything.", file=sys.stderr)
        import urllib.request, urllib.parse
        since = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=a.days)).timestamp())
        recs, url = [], "https://api.stripe.com/v1/charges?limit=100&created[gte]=%d" % since
        while url:
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + k})
            try:
                with urllib.request.urlopen(req, timeout=30) as f:
                    d = json.loads(f.read().decode())
            except Exception as e:
                die(f"Stripe read failed: {e}. Nothing was written and nothing was sent anywhere.", 4)
            for c in d.get("data", []):
                if c.get("status") == "succeeded": continue
                o = c.get("outcome") or {}
                pm = ((c.get("payment_method_details") or {}).get("card") or {})
                recs.append({"advice": o.get("advice_code") or o.get("network_advice_code") or "",
                             "key": pm.get("fingerprint") or c.get("customer") or c.get("id"),
                             "ts": c.get("created"), "amount": c.get("amount"),
                             "currency": c.get("currency") or ""})
            if d.get("has_more") and d.get("data"):
                url = "https://api.stripe.com/v1/charges?limit=100&created[gte]=%d&starting_after=%s" % (since, d["data"][-1]["id"])
            else:
                url = None
        pop = f"Stripe API, declined charges in the last {a.days} days"

    report(analyse(recs), pop)
    print()
    print("  Nothing left this machine. This script makes no write call of any kind.")

if __name__ == "__main__":
    main()
