# Backlog

## Multi-issuer JWT verification (Azure AD / Google / generic OIDC)

**Context.** The identity chain (web-a2a -> config-a2a -> mcp-fs) is full JWT.
Today the verifiers (mcp-fs, config-a2a) load a single static `public_key_path`
and web-a2a self-mints the token. This is the local posture without a real IdP.

**Goal.** When a token arrives from a real provider, detect the issuer and verify
against that provider's published keys, using the right library, with no static
key file.

**Scope.**
- Add a `jwks_url` verification option next to `public_key_path` (selected by
  config) in mcp-fs `JwtConfig` and config-a2a `ServerJwtConfig`.
- Detect/route by `iss`: support several trusted issuers at once (a small
  issuer -> {jwks_url, audience, email_claim} registry), so Azure and Google can
  coexist.
- Use the provider-aware libraries rather than hand-rolling JWKS fetch/rotation:
  - generic: `PyJWT` `jwt.PyJWKClient(jwks_url)` (fetch + kid selection + cache),
  - Google: `google-auth` (`google.oauth2.id_token.verify_oauth2_token`),
  - Azure AD: `MSAL` / `azure-identity` plus PyJWT, or `authlib` OIDC discovery
    (`.well-known/openid-configuration`).
- Enforce `audience` (your registered API/app id) and `issuer`; read the email
  claim per provider (`email` for Google, often `preferred_username`/`upn` for
  Azure).
- web-a2a stops minting and forwards the IdP token it received (OIDC /
  oauth2-proxy) on `X-Forwarded-Authorization`.

**Unchanged.** Wire format (Bearer on `X-Forwarded-Authorization`), the strict
single-mode design, the pass-through in config-a2a, and the email-claim
authorization in mcp-fs. Only the key source moves from a static PEM to a JWKS
URL, and web-a2a relays instead of mints.
