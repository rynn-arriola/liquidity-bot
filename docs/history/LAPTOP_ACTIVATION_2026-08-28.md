# Laptop production activation — 2026-08-28

The operator selected the laptop as the primary live runtime, confirmed the
desktop was off and explicitly authorized startup. The former server had
already been destroyed.

Before startup, the Windows checkout, GitHub `main` and WSL checkout matched
`04fddb122010a6e4ebb3eddd5be00608ece31c06`. The commit adds an isolated
read-only TV dashboard to the previously accepted trading core. The complete
WSL suite passed 785 tests. SQLite integrity, restricted credentials, quote
size 10, AC power, public Retail health and US residential WSL egress passed.
An authenticated 00:39Z account snapshot showed zero working orders and zero
positions.

The launcher started the supervisor and runner at 00:40Z and the independent
TV dashboard after runner proof. At 00:42Z one coordinated authenticated
dashboard read showed 20/20 quoting markets, 39 resting orders, zero positions,
zero absent/unresolved state and no risk stop. The recent log contained no
403, permission/location denial, critical or recovery event. This proves the
laptop could mutate the Retail account where the destroyed server could not.

The laptop is the sole writable ledger/API owner. The desktop remains off as a
manual cold backup and must never run concurrently. A future handoff requires
a graceful stop, authenticated venue proof and a verified stopped-runtime
ledger snapshot.

At 00:44Z and 00:45Z, private shards 1 and 3 naturally crossed their silence
deadlines. Both overlap recoveries completed with `cancels=0`, the same runner
PID and all 39 orders preserved; local state remained 20 markets, zero
positions, zero unresolved intents and zero unknown orders.
