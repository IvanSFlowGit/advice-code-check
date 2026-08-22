# advice_code_check

How often does your retry logic contradict the issuer?

Stripe returns an issuer instruction on every declined charge at
`outcome.advice_code`: `do_not_try_again`, `try_again_later`,
`confirm_card_data`. Most dunning systems schedule retries off a hand-built map
of decline **codes** instead.

A decline code is a claim about a code. The advice code is the issuer's
instruction about **this charge**, and the same decline code carries different
advice on different transactions. So a retry can contradict an instruction the
issuer already gave.

This counts how often that happened in your own data. Then it counts the other
direction, which is usually the expensive one: charges the issuer said **try
again later** that were never attempted again.

The first is wasted effort. The second is revenue nobody went back for, on an
instruction that was already there and free.

## Get it and run it

```
curl -O https://raw.githubusercontent.com/IvanSFlowGit/advice-code-check/main/advice_code_check.py
python3 advice_code_check.py --csv declines.csv
```

Read it first. It is one file, no dependencies, and it is short enough to read in
the time it takes to decide whether to trust it. Do not pipe it into an
interpreter, from here or from anywhere.

## What the output looks like

Real shape, synthetic numbers:

```
  POPULATION: declines.csv, column 'advice_code' for the advice code,
              'card_fingerprint' as the card key
  declined charges examined: 7

  issuer instruction, as returned by Stripe:
    do_not_try_again          3   42.9%
    try_again_later           2   28.6%
    confirm_card_data         1   14.3%
    (none returned)           1   14.3%

  charges the issuer said DO NOT TRY AGAIN:        3
  of those, a later attempt hit the same card:     2
  distinct cards involved:                         1

  => 66.7% of do_not_try_again charges were followed by
     another attempt on the same card.

  value sitting on those charges, at the amount of the refused attempt:
           98.00 EUR

  --------------------------------------------------------------------

  THE OTHER DIRECTION, WHICH IS USUALLY THE EXPENSIVE ONE.

  charges the issuer said TRY AGAIN LATER:          2
  of those, NEVER attempted again on that card:     1
  distinct cards involved:                          1

  => 50.0% of try_again_later charges were never retried.

  value on charges the issuer invited you to retry and nobody did:
           29.00 EUR
```

The two lines that matter are the last two, and both are hedged in the output
itself rather than in a footnote. See below for why.

The dashboard export usually will not carry the advice code. That is the normal
case, and the script tells you so rather than guessing. Use the API instead:

1. Stripe dashboard, Developers, API keys, **Create restricted key**
2. Give it **READ on Charges**. Nothing else. Leave every other permission at None.
3. `STRIPE_API_KEY=rk_... python3 advice_code_check.py --api --days 90`

A restricted read key cannot write, refund, charge or move money.

## What it does not do

- No write call of any kind, in any mode.
- No network call at all in CSV mode.
- Nothing is sent anywhere. It prints to your terminal and exits.

Read it before you run it. It is one file and about two hundred lines.

## What it prints, and what the numbers are not

The distribution of the issuer's own instruction across your declines, then the
number that matters: how many charges the issuer said **do not try again** were
followed by another attempt on the same card.

**That figure is a ceiling, not a count.** A later attempt on the same card looks
identical from the outside whether your dunning system scheduled it or the
customer tried again themselves. Treat it as the largest the problem could be,
then check a handful by eye against your own retry log to find where it sits.

**The abandoned figure is a ceiling too.** Some of those customers cancelled,
refunded, paid by another route, or were retried outside the window your export
covers. What it measures is the population your dunning did not come back to, on
charges where the issuer said coming back was worth trying.

**The money figure is not recoverable revenue.** It is the value attached to
charges the issuer had already refused for good, so it measures the size of the
population being retried rather than money on the table. Some of it is
recoverable with a new card. None of it with the same one.

## Tests

```
python3 test_advice_code_check.py
```

Nineteen cases, no dependencies, every fixture synthetic. They cover the things
that would quietly change the answer rather than break the run: a succeeded
charge must not enter the population, an attempt BEFORE the refusal is not a
contradiction, a later attempt on a different card is not one either, one card
counts once however many retries follow, and the money is the amount of the
refused attempt rather than the retry.

Three of them assert that the hedges are still in the output. The ceiling
warning and the not-recoverable-revenue warning are load-bearing, and if they
could be dropped without a test going red, eventually somebody would drop them.

The suite has been mutation tested rather than merely run, on both halves.
Removing the forward-in-time comparison from the contradiction detector turns it
red with "an attempt BEFORE the refusal cannot have been caused by it". Removing
it from the abandoned detector turns it red too, rather than silently reporting
every charge as never retried.

It also caught a real defect while being written: a population containing only
`try_again_later` returned early and never printed the abandoned section at all,
so the half that finds money was invisible in exactly the case where it was the
only thing to say.

## Requires

Python 3.8+. No dependencies.

## Licence

MIT.
