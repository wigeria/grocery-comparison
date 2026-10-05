# Caddy with the DuckDNS module, used to prove ownership of the DuckDNS name
# for the HTTPS certificate without exposing the server to the internet.
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/caddy-dns/duckdns

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
