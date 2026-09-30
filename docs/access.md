# Access and troubleshooting

Your API key identifies you; your institution's Scopus subscription entitles
you. Elsevier recognizes the subscription by the institution's IP range or by
an institutional token, and entitles each API separately.

## Run `diagnose_connection` first

It checks reachability, metadata, search, reference lists (REF view),
ScienceDirect full text and journal metrics, then names the tools that cannot
work with your current access and says why. It reports where your credentials
came from, never their values.

## The misleading failure

Off campus without a token, ID-based metadata keeps working while search,
citations, references and full text fail. Search fails misleadingly: every
query, even `ALL(gene)`, returns `400 "Error translating query"`, which reads
like a syntax error. The server adds a note to that error pointing at
entitlement.

## Getting subscriber access off campus

1. **Institutional VPN.** No configuration needed.
2. **Institutional token** (`SCOPUS_INSTTOKEN`), requested through your
   library or Elsevier support and linked to your API key. Works from any
   network. Store it in the [OS secret store](configuration.md#keep-secrets-out-of-config-files).
3. **SOCKS tunnel to an on-campus host**, where your institution permits it.
   Only Elsevier traffic uses the tunnel:

   ```bash
   ssh -N -D 1080 you@host.your-university.edu
   ```

   then set `SCOPUS_PROXY` to `socks5h://127.0.0.1:1080`.

Or use `source="openalex"`, which needs no subscription (see
[data sources](data-sources.md)).

## Other things `diagnose_connection` distinguishes

- **Token configured but refused:** the token is not linked to your API key,
  or has been revoked.
- **Proxy configured but unentitled:** the proxy's exit IP is outside your
  institution's range, or the tunnel is down.
- **Slow network:** requests are retried with backoff (`SCOPUS_MAX_RETRIES`),
  and the verdict warns when the path to Elsevier is degraded.
