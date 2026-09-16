# Findings index rollover — September 15, 2026

Preserved historical index detail; not current runtime truth.

- **Changelog monitoring belongs outside trading.** The public RSS feed can be polled by a lock/timeout-bounded process with independent state and Discord notification. It needs no API key, ledger access, REST budget or runner hook. **Retail list volume fields were absent on the observed 2026-08-28 wire.** The first implementation silently selected no pure markets. Bounded discovery now ranks homogeneous full-book `sharesTraded`; its 10-minute acceptance covered 100 markets, 24,050 books and 792 trades at 99.3% stream coverage with no gaps/reconnects/errors/REST 429s and no effect on the live runner.
