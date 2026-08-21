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

This counts how often that happened in your own data.

## Run it

```
python3 advice_code_check.py --csv declines.csv
```

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

**The money figure is not recoverable revenue.** It is the value attached to
charges the issuer had already refused for good, so it measures the size of the
population being retried rather than money on the table. Some of it is
recoverable with a new card. None of it with the same one.

## Requires

Python 3.8+. No dependencies.

## Licence

MIT.
