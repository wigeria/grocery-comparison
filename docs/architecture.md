# Architecture

How the app is put together, and why some things are the way they are. For setup, see the
README.

## What it's for

I wanted to order groceries from my phone by typing a list or taking a photo of one, without
ever risking an order I hadn't checked. It also checks Blinkit, since prices and especially
delivery fees vary a lot between the two. It runs on a spare laptop at home and is only
reachable on the home network.

## Overview

```
Phone browser
    |  HTTPS (Caddy, Let's Encrypt via DuckDNS)
    v
FastAPI app --> OrderService (orders.py)
                  |
                  +--> cart_agent.py ----> Claude Code --> Zepto MCP   (cart tools only)
                  +--> zepto_client.py ------------------> Zepto MCP   (view, clear, preview, place)
                  +--> comparison.py ----> Claude Code --> Blinkit search (matching)
                  |       +--> blinkit_client.py --------> Blinkit cart pricing
                  +--> db.py -----------------------------> SQLite (db/)
```

`orders.py` is where all the order rules live. `cart_agent.py` is the Claude session that
turns chat messages and photos into cart changes. `zepto_client.py` talks to Zepto directly
for anything that needs to be exact, and `zepto_auth.py` handles the OAuth login and token
refresh. `comparison.py` and `blinkit_client.py` do the Blinkit price check. `db.py` is a thin
SQLite layer with a small migration runner, and `api.py` plus `static/` are the HTTP routes and
the web page.

## Claude vs plain code

Claude is good at the fuzzy part. It can turn "milk, eggs, brown bread" or a messy photo into
actual products, use past orders to pick the brands you usually buy, and figure out that a
1 L pack on Blinkit is the same as two 500 ml pouches on Zepto.

The parts that have to be exact don't go through Claude at all. Reading the cart, emptying it,
getting the total and placing the order are all direct calls to Zepto from the app's own code,
so none of it depends on how a model reads a tool result.

## Not ordering the wrong thing

These are real orders, so I assumed Claude, Zepto's responses and the network could all go
wrong at some point.

Claude simply can't order. Its session has no built-in tools and doesn't load any user
settings, and Zepto's ordering and payment tools are taken out of its context. On top of that,
every tool call goes through a permission check that only allows cart and catalog tools. The
same check rejects any address other than the configured one and forces the app's device id
on cart updates. Zepto's tool results include instructions meant for the model ("ask which
payment method", that sort of thing), so the system prompt tells Claude to ignore those.

Approving only works for what you actually reviewed. Reviewing fetches the live cart plus
Zepto's cash on delivery preview (the real total, fees included) and stores a hash of the
items, quantities, prices and total. Approve has to send that token back. The app then reads
the cart and preview again, checks the address, payment method and that it's deliverable, and
only orders if the hash still matches. If something changed, say someone edited the cart from
the Zepto app, you get the new review instead. Sending a chat message or touching the cart
throws the old review away.

Failures don't get retried. If placing an order fails or gets cut off, it's marked as failed
with a note to check the Zepto app, because it might have gone through anyway. The app records
the order as placed the moment Zepto confirms it, before the connection closes, so an error
while closing can't hide an order that worked. If the server crashes mid-order, the next start
marks that order as failed too.

And only one thing happens at a time. There's only ever one active order, and everything that
touches the cart runs under a single lock, so a chat message, a review and an approval can't
overlap.

## Zepto

Zepto has an official MCP server meant for AI clients. The app uses it two ways: Claude Code
connects to it as an HTTP MCP server while building the cart, and `zepto_client.py` connects
with the Python MCP SDK for the exact steps.

A few things I found out along the way:

- The cart belongs to the Zepto account, not the MCP session, so separate sessions all see the
  same cart.
- The store is picked per session, so every session has to select the delivery address first.
- `create_order` with `confirmOrder: false` gives you a full preview without ordering.
- OAuth only allows `localhost` redirects. Login happens once over SSH with a forwarded port
  (`scripts/zepto_login.py`), and refresh tokens take care of it after that.

## Blinkit (experimental)

Blinkit doesn't have a public API. `blinkit_client.py` makes the same read-only requests the
Blinkit website makes in a browser, based on the research in
[yniks/blinkit-mcp](https://github.com/yniks/blinkit-mcp). It never logs in or touches a Blinkit
account or cart, and never orders. Blinkit's cart endpoint doesn't store anything either. You
send it a list of items and it sends back the price.

The comparison has two steps. First a short Claude session gets the Zepto cart and one tool
that searches Blinkit, and works out which Blinkit product (and how many) matches each item.
It flags whether each match is exact and notes any difference, like a different brand or pack
size. Then the app prices those items through Blinkit's cart endpoint and compares the two
totals with delivery and other charges included, because the fees are often what decides it.

This part is unofficial and will likely break whenever Blinkit changes their site. It's for
personal, occasional use, it isn't affiliated with Blinkit, and it uses website prices, which
can be different from what the Blinkit app shows.

## Data

`db/zepto_ordering.sqlite3` holds orders, chat messages, reviews and comparisons. Migrations
are numbered SQL files in `db/migrations/`, applied on startup, one transaction each.

`data/` has the Zepto tokens (written atomically, readable only by the owner), uploaded photos
(shrunk to 1568 px at most), and Claude Code's session files so a chat can pick up again after
a restart.

`config.toml` has the delivery address id and coordinates. It's gitignored.

## Deployment

Docker Compose runs two containers. The app only listens on Docker's internal network, and
Caddy serves HTTPS on 80 and 443 and forwards to it. The certificate comes from Let's Encrypt
through a DNS challenge on a DuckDNS name, so the server never needs to be reachable from
outside. The name just points at the server's LAN address.

## Known limitations

- There's no login. Anyone on the home network can use it and place orders, so it shouldn't be
  exposed to the internet as it is.
- The Zepto cart is shared with the Zepto app on the same account. If someone edits it in the
  few seconds between the last check and the order going in, there's no way to fully stop that.
- Blinkit prices come from the website, not the app.
- It only works in India, since that's where both stores are.

## Tests

The tests in `tests/` don't need a network. Zepto, Claude and Blinkit are all replaced with
in-memory fakes. They cover the order flow and its failure cases, the tool permission check,
parsing of real Zepto responses, token refresh, migrations and the HTTP API.
`scripts/demo_server.py` runs the whole web app on the same fakes.
