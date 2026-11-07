#!/bin/bash
# Tradovate host probe: WebSocket ping/pong round trip, no login, no API budget.
# Pongs come from Tradovate's servers (about 28 ms from Virginia, while the
# Google edge is about 2 ms away), so this measures distance to Tradovate.
# Needs only bash 5 and openssl. Usage: ./wsprobe.sh [host] [samples]
#   ./wsprobe.sh demo.tradovateapi.com 200
H=${1:-demo.tradovateapi.com}; N=${2:-40}; P="WSPROBE ${H%%.*} $(hostname)"
now() { local t=${EPOCHREALTIME/./}; echo $((10#$t)); }
ms() { printf '%d.%03d' $(( $1 / 1000 )) $(( $1 % 1000 )); }
frame() { # opcode byte, payload (<126), zero mask
  printf "\\x$1\\x$(printf %02x $((0x80 | ${#2})))\\x00\\x00\\x00\\x00%s" "$2"
}
t0=$(now)
coproc WS { openssl s_client -quiet -connect "$H:443" -servername "$H" 2>/dev/null; }
printf 'GET /v1/websocket HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n' "$H" >&"${WS[1]}"
IFS= read -r -t 5 status <&"${WS[0]}"; t1=$(now)
echo "$P status ${status%$'\r'} connect+tls+upgrade_ms $(ms $((t1-t0)))"
while IFS= read -r -t 5 line <&"${WS[0]}"; do [ "${line%$'\r'}" = "" ] && break; done
IFS= read -r -t 5 -n 3 first <&"${WS[0]}"
rtts=()
for i in $(seq 1 "$N"); do
  frame 89 "p${i}Z" >&"${WS[1]}"; ts=$(now); got=""
  while IFS= read -r -t 3 -d 'Z' chunk <&"${WS[0]}"; do [[ "$chunk" == *"p$i" ]] && { got=1; break; }; done
  te=$(now)
  if [ -n "$got" ]; then rtts+=($((te-ts))); else echo "$P ping_no_pong $i ${chunk:0:60}"; break; fi
  [ $((i % 8)) = 0 ] && frame 81 "[]" >&"${WS[1]}"
  sleep 0.2
done
if [ ${#rtts[@]} -gt 0 ]; then
  s=($(printf '%s\n' "${rtts[@]}" | sort -n)); c=${#s[@]}
  echo "$P ping n $c min $(ms ${s[0]}) p50 $(ms ${s[$((c/2))]}) p90 $(ms ${s[$((c*9/10))]}) max $(ms ${s[$((c-1))]})"
fi
kill "$WS_PID" 2>/dev/null; echo "$P done"
