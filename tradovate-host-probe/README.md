# Tradovate host probe

Finds which hosting location is closest to Tradovate's API servers, for Round 1 of the host selection in Copytrader `docs/architecture/latency-decisions.md`.

`demo.tradovateapi.com` and `live.tradovateapi.com` are Google Cloud anycast addresses, so ping and TCP connect time only measure the distance to the nearest Google edge. This probe opens the Tradovate WebSocket (`/v1/websocket`) and times WebSocket ping → pong. Pongs come from Tradovate's servers, so this measures the real distance. No login, no token, and no API request budget.

```bash
./wsprobe.sh demo.tradovateapi.com 200
./wsprobe.sh live.tradovateapi.com 200
```

Needs bash 5 and openssl (both present on stock Ubuntu/Debian images and Heroku). Run every candidate host **in the same hour**, then compare `p50` and `p90`. The 200 ms gap between pings keeps the load trivial.

Output lines start with `WSPROBE <host> <machine>`: connect + TLS + upgrade time, then ping statistics in milliseconds. Timing includes a small bash/openssl overhead, which is the same on every host.

First result (2026-11-07, Heroku `us` = AWS us-east-1 Virginia): ping p50 28.0 ms (demo), 27.8 ms (live).
