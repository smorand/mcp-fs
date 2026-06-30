# Identity and authorization

mcp-fs decides, for every call, two separate questions. Keep them distinct: most
confusion comes from conflating them.

| Question | Mechanism | Failure |
|----------|-----------|---------|
| **Authentication**: is the caller who they claim? | verify a signed RS256 bearer token | `401 ERR_UNAUTHENTICATED` |
| **Authorization**: may this identity act on this project? | match the identity against the project ACL | `ERR_FORBIDDEN` (HTTP 200, MCP `isError`) |

A `401` means the token is missing or invalid. `ERR_FORBIDDEN` means the token was
valid (the identity is authenticated) but that identity is not on the project's
ACL. They are never the same thing.

## How identity is received (authentication)

`IdentityMiddleware` (pure ASGI) protects the `/mcp` prefix:

1. Read the token from `auth.jwt.header` (default `X-Forwarded-Authorization`);
   require a `Bearer ` prefix.
2. **Verify** it with the public key (`auth.jwt.public_key_path`): RS256 signature,
   `issuer` (default `web-a2a`), expiry, and `audience` if configured. This is real
   signature verification, not a bare decode; a forged, tampered or expired token
   is rejected. There is no trust header and no fallback: an `X-Forwarded-User`
   alone yields `401`.
3. Read the identity from `auth.jwt.username_claim` (default `email`).
4. **Casefold** it (`normalize_identity`) and bind it to a `ContextVar`;
   `current_person()` returns it to the tools.

The upstream signer (web-a2a, or later a real IdP) holds the private key; mcp-fs
only verifies with the public key.

## The ACL model (authorization)

Authority is by identity, with a strict hierarchy: **platform admin > owner > member**.

- **Platform admin**: listed in `auth.admins` (config). May create projects
  (designating any owner), list every project and user, and act on any project.
- **Owner**: set when the project is created; also a member. One per project.
- **Member**: a person authorized on a project.

`ctx.authorize(mount_id)` runs `require_member` before any `fs.*` tool returns the
volume; `admin.*` tools use `require_owner` / admin checks. A wrong `mount_id`
yields `ERR_FORBIDDEN`, so letting the model choose the mount is safe as long as
the identity is correct: a token only ever grants access to **that person's**
projects.

## Caseless matching

Identity (the email) is matched **case-insensitively**. `normalize_identity`
casefolds it at reception, the ACL store casefolds owner/member values on write
and casefolds the queried identity on read, and `is_admin` casefolds both sides.
So `Seb.Morand@Gmail.com`, `seb.morand@gmail.com` and `SEB.MORAND@GMAIL.COM` are
the same principal. Everything else (paths, `project_id`, tool names) stays
case-sensitive.

## Managing authorization

- **Admins**: edit `auth.admins` in `config/local.yaml`, then restart (the config
  is read at startup).
- **Owner**: `admin.create_project(project_id, owner=<email>)`.
- **Member**: `admin.add_member(project_id, person=<email>)` /
  `admin.remove_member(project_id, person=<email>)` (the owner cannot be removed).
- **Discover**: `fs.list_allowed_roots` returns exactly the projects the current
  identity can access (`[{mount_id, owner}]`). An empty list means the identity is
  on no project; provision it with one of the steps above. Consumers use this to
  populate a project selector.

## Providers

- **Today**: tokens are minted upstream by web-a2a (RS256, claim `email`, issuer
  `web-a2a`), verified here with a static public key
  (`auth.jwt.public_key_path`). Generate the demo keypair with
  `scripts/gen-jwt-demo-keys.sh`; mint a token for a curl test with
  `scripts/mint-token.sh <email>`.
- **Future** (`specs/BACKLOG.md`): verify Azure AD / Google tokens by issuer
  against the provider JWKS (`jwks_url` next to `public_key_path`). The ACL model,
  the caseless matching, and the wire format are all unchanged; only the key
  source moves from a static PEM to the provider's published keys.
