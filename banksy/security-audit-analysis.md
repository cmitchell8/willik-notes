# Security Audit Analysis: Banksy Auth Architecture

## Table of Contents

- [Background](#background)
- [The Audit's Central Recommendation: Banksy as a Resource Server](#the-audits-central-recommendation-banksy-as-a-resource-server)
  - [What This Changes and What It Doesn't](#what-this-changes-and-what-it-doesnt)
  - [Open Questions](#open-questions)
- [Ticket-by-Ticket Assessment](#ticket-by-ticket-assessment)
  - [Tickets Safe to Implement Now](#tickets-safe-to-implement-now)
  - [The Migration Ticket](#the-migration-ticket)
- [Mural-OAuth Mode: Security and Migration Analysis](#mural-oauth-mode-security-and-migration-analysis)
  - [How mural-oauth Differs from sso-proxy](#how-mural-oauth-differs-from-sso-proxy)
  - [Security Posture: Ticket Extrapolation Summary](#security-posture-ticket-extrapolation-summary)
  - [New Security Concerns](#new-security-concerns)
  - [Resource Server Migration: Impact on mural-oauth](#resource-server-migration-impact-on-mural-oauth)
  - [Practical Assessment](#practical-assessment)
- [Outstanding Research](#outstanding-research)
  - [IDE Support for Protected Resource Metadata (Resolved)](#ide-support-for-protected-resource-metadata-resolved)
  - [External IdP Selection](#external-idp-selection)
  - [The Non-Enterprise User Gap](#the-non-enterprise-user-gap)
    - [Resolution Option 1: Dedicated IdP with Mural as a Custom Social Connection](#resolution-option-1-dedicated-idp-with-mural-as-a-custom-social-connection)
    - [Resolution Option 2: Mural Evolves Its OAuth Infrastructure](#resolution-option-2-mural-evolves-its-oauth-infrastructure)
    - [Resolution Option 3: Dual Auth Architecture](#resolution-option-3-dual-auth-architecture)
    - [Resolution Synthesis](#resolution-synthesis)
- [Relationship to the FastMCP Migration](#relationship-to-the-fastmcp-migration)
- [Summary of Recommended Sequencing](#summary-of-recommended-sequencing)

## Background

A security audit in early 2026 produced eight tickets (critical, high, medium) and a design preamble recommending Banksy adopt an OAuth Resource Server posture under FastMCP. This document analyzes those findings and the research completed to inform migration direction.

Banksy supports auth modes `sso-proxy`, `mural-oauth`, and `m2m` (configured via `AUTH_MODE`, one per deployment). The audit targeted `sso-proxy` only — `mural-oauth` was introduced afterward. This document covers both: the original audit analysis and an extrapolation to mural-oauth.

Banksy's auth is two stacked OAuth-like layers:

- **Layer 1 (IDE → Banksy):** In sso-proxy, MCP OAuth + Google OAuth (via SSO proxy) establishes a session and issues MCP tokens. In mural-oauth, the MCP flow redirects to Mural's consent page — Mural serves as both IdP and API token source, collapsing two layers into one user-facing flow.
- **Layer 2 (Banksy → Mural):** In sso-proxy, a session-activation code/nonce pattern lets the browser perform Mural OAuth; Banksy claims tokens (code + server-only nonce) for server-side use. In mural-oauth, Layer 2 is embedded in the Layer 1 flow — the authorization code grant with Mural yields identity (via `/api/public/v1/users/me`) and API tokens (access + refresh) stored for Mural API calls.

Each ticket has two categories: "fix in current flow" (harden without changing architecture) and "FastMCP design direction" (assumes migration). They carry different risk profiles and timelines.

---

## The Audit's Central Recommendation: Banksy as a Resource Server

The audit recommends Banksy stop operating as an OAuth **Authorization Server** (AS) — issuing tokens — and instead become a **Resource Server** (RS) that validates tokens issued by an external IdP. ("Resource server" is OAuth 2.0 terminology, unrelated to MCP Resources.)

Today Banksy is an AS: Better Auth's MCP plugin serves `/.well-known/oauth-authorization-server`, handles DCR, and issues MCP tokens. As an RS, Banksy would serve `/.well-known/oauth-protected-resource` (RFC 9728) pointing IDEs to an external IdP. The IDE authenticates with the IdP, gets a JWT, and presents it on every MCP request. Banksy validates signature (via JWKS), issuer, audience, and expiration, then authorizes tool calls based on claims and scopes.

The rationale: running an AS exposes confused-deputy attacks, DCR abuse, and authorization code interception. Eliminating the AS eliminates those surfaces. Ticket 3 is the concrete expression — remove Better Auth's MCP plugin and `/api/auth/mcp/*`.

### What This Changes and What It Doesn't

The RS model only affects Layer 1 (IDE → Banksy authentication). Layer 2 is unchanged: Banksy still stores and uses Mural API tokens. The IDE-presented JWT answers "is this a legitimate user?" but doesn't provide Mural tokens.

This two-layer separation is inherent, not a consequence of IdP choice. Using Mural as the IdP would eliminate Layer 2 but is not viable (see three blockers in External IdP Selection). This applies to mural-oauth as well — despite combining both layers today, the RS migration causes the two modes to converge (see Resource Server Migration: Impact on mural-oauth).

Post-migration flow (both modes):

1. **One-time setup (browser):** User authenticates with external IdP (Layer 1), completes Mural OAuth (Layer 2). Banksy stores Mural tokens.
2. **Every MCP request (IDE):** IDE presents an IdP-issued JWT. Banksy validates it, looks up Mural tokens in Postgres, executes the tool call, returns the result.

### Open Questions

Three questions the audit doesn't answer. The first and third are resolved; the second remains open.

**Who is the external IdP? (Resolved — see External IdP Selection)** Google ID tokens are JWTs but lack custom audiences/scopes. Mural's tokens are functionally opaque to external validators (resolves Risk 1 from the FastMCP auth strategy plan). A dedicated IdP (Auth0, Azure AD) gives full control but adds infrastructure. Full assessment in External IdP Selection.

**Does the IdP cover all Mural user segments? (Open — see Non-Enterprise User Gap)** Fails for Mural self-serve users (email/password). Google excludes non-Google users; a standalone dedicated IdP requires a new account. Resolution: dedicated IdP with Mural as a custom upstream OAuth provider — depends on PoC validation. Provider evaluation scoped separately (`banksy/.cursor/prompts/research-auth-provider-alternatives.md`).

**Do IDEs support PRM? (Resolved — see IDE Support for PRM)** The 2025-11-25 MCP spec makes PRM mandatory. Cursor, VS Code (Copilot), and Claude Desktop all support it. Zed and Continue.dev lack remote MCP OAuth entirely but can't use Banksy's current AS model either — no regression.

---

## Ticket-by-Ticket Assessment

### Tickets Safe to Implement Now

Seven of eight tickets have "fix in current flow" recommendations safe to ship independently of FastMCP.

**Ticket 1 (Critical) — OAuth Login CSRF:** sso-proxy's Google OAuth callback logs a warning on state validation failure but continues to create a session. Fix: generate crypto-secure state server-side, store with TTL, reject on mismatch/expiration, enforce single-use. *Mural-oauth:* Substantially addressed — uses HMAC-SHA256 state with `crypto.timingSafeEqual` and 10-minute TTL, a major improvement. Gap: state is not single-use (replayable within TTL window). Mitigated by Mural's single-use authorization codes, but defense-in-depth calls for server-side nonce tracking.

**Ticket 2 (Critical) — Open Redirect:** `url.startsWith('/')` accepts protocol-relative URLs like `//evil.com`. Fix: parse with `new URL(url, baseOrigin)`, require `u.origin === baseOrigin`. *Mural-oauth:* Same vulnerability in `isValidCallbackUrl` — the `startsWith('/')` early return bypasses the origin comparison branch. Same fix.

**Ticket 4 (High) — Security Headers:** Auth pages lack CSP, HSTS, Referrer-Policy, anti-framing headers. Purely additive (Azure Front Door or middleware). *Mural-oauth:* Applies equally — same infrastructure, same gap.

**Ticket 5 (High) — Mural Claim Race Condition:** `/auth/mural/claim` reads, claims, saves, deletes in non-atomic steps — concurrent claims can both succeed. Fix: atomic compare-and-set with `claimedAt`/`status` column. *Mural-oauth:* No `/auth/mural/claim` endpoint. Token storage uses `INSERT ... ON CONFLICT DO UPDATE` (atomic) — significantly safer. Lower risk, verify under concurrent load.

**Ticket 6 (High) — Plaintext Refresh Tokens:** Mural tokens stored unencrypted in Postgres. Fix: envelope encryption with Azure Key Vault (transparent encrypt-on-write, decrypt-on-read). *Mural-oauth:* `muralOauthToken` stores tokens as plain text, same as `muralSessionToken`. Shared abstraction in `mural-tokens.ts` means one fix covers both tables.

**Ticket 7 (Medium) — OAuth Codes in URL History:** SPA doesn't call `history.replaceState()` after reading the authorization code. One-line fix. *Mural-oauth:* Same gap in `oauth-callback.tsx`.

**Ticket 8 (Medium) — Sensitive Auth Logging:** Auth flows log truncated state/code values. Fix: replace with correlation IDs. *Mural-oauth:* Less severe (doesn't log tokens or codes) but logs full `callbackURL` and passes `error_description` to client. Fix: hash or boolean for callback URLs, generic error to browser, `error_description` server-side only.

### The Migration Ticket

**Ticket 3 (High) — Remove Legacy MCP OAuth Surface:** The only ticket that breaks existing behavior — remove Better Auth's MCP plugin, `/api/auth/mcp/*`, and `/.well-known/oauth-authorization-server`. This is the migration itself, scoped as a ticket. IDE compatibility confirmed for Cursor, VS Code, Claude Desktop. Prerequisites: select external IdP, implement replacement via `RemoteAuthProvider` or `OAuthProxy`. Execute only after replacement is deployed and tested. *Mural-oauth:* Applies equally — same plugin, removed from both modes simultaneously.

---

## Mural-OAuth Mode: Security and Migration Analysis

### How mural-oauth Differs from sso-proxy

Introduced after the audit. The MCP OAuth flow redirects to Mural's consent page; after authorization, an SPA exchanges the code for Mural OAuth tokens (access + refresh). The callback plugin fetches user info from Mural's public API, creates/finds a Better Auth user, stores tokens in Postgres, and completes the MCP flow.

Key difference from sso-proxy: identity and API access come from the same source (Mural) via a single OAuth flow, vs. sso-proxy's two-step flow (Google sign-in + Mural session activation). Uses the Mural Public API (`/api/public/v1/`) rather than internal content endpoints. Tokens stored in `muralOauthToken` (vs. `muralSessionToken`), token type `oauth` (vs. `session`).

Despite appearing to collapse the two layers, Better Auth's MCP plugin still serves as the AS (`/.well-known/oauth-authorization-server`), and the Mural OAuth flow is embedded within the MCP flow. This matters for the RS migration.

### Security Posture: Ticket Extrapolation Summary

Six of eight tickets apply to mural-oauth with varying severity.

**Direct apply (same fix):** Ticket 2 (open redirect — identical `startsWith('/')` bug), Ticket 4 (security headers), Ticket 6 (plaintext tokens in `muralOauthToken` — persists post-migration), Ticket 7 (code not scrubbed from history in `oauth-callback.tsx`).

**Partially addressed:** Ticket 1 (CSRF) — substantially mitigated via HMAC-SHA256 state with nonce, timestamp, 10-minute TTL, and `crypto.timingSafeEqual`. Materially stronger than sso-proxy's "log and continue." Gap: state is not single-use (replayable within TTL). Mitigated by Mural's single-use codes; defense-in-depth calls for server-side nonce tracking.

**Reduced applicability:** Ticket 5 — no `/auth/mural/claim` endpoint; token storage uses atomic `INSERT ... ON CONFLICT DO UPDATE`. Ticket 8 — doesn't log tokens/codes but logs `callbackURL` and passes `error_description` to client; clean up both.

**Identical:** Ticket 3 applies equally — same Better Auth MCP plugin.

### New Security Concerns

Concerns introduced by mural-oauth beyond the eight audit tickets:

**State replay window.** HMAC-signed state is replayable for 10 minutes — no server-side nonce storage or consumption tracking. Mural's single-use codes limit practical impact, but the window is wider than necessary. Fix: store used nonces in Redis/DB with TTL matching `STATE_MAX_AGE_MS`, reject seen nonces.

**Better Auth secret as CSRF root of trust.** State HMAC is keyed with `ctx.context.secret`. Compromise enables forging valid state for arbitrary callbacks (login CSRF). Not a vulnerability per se — any HMAC scheme depends on secret integrity — but makes this secret a higher-value target than in sso-proxy mode.

**SPA error parameter reflection.** `oauth-callback.tsx` renders `error` and `error_description` from URL params. React's text escaping prevents XSS when rendered as text, but unsanitized values risk phishing if ever rendered as HTML. Fix: generic error to user, log `error_description` server-side only.

**Scope breadth.** Default scopes (`murals:read`, `murals:write`, `workspaces:read`, `rooms:read`, `identity:read`, `templates:read`) are broad. Overridable via `MURAL_OAUTH_SCOPES` but no runtime validation against tool requirements. Audit for minimum privilege.

**Client credentials in environment.** `MURAL_OAUTH_CLIENT_ID` and `MURAL_OAUTH_CLIENT_SECRET` as env vars. Handled appropriately (secret used only for token exchange and HMAC, client ID truncated in logs). Should be rotated periodically and stored in Azure Key Vault for production.

### Resource Server Migration: Impact on mural-oauth

The RS migration causes mural-oauth and sso-proxy to converge architecturally — the most significant finding of this analysis.

Today mural-oauth provides single-step UX: one Mural consent yields identity + API access. Under RS, Layer 1 requires an external IdP with JWKS-validatable tokens. Mural doesn't qualify (three blockers in External IdP Selection apply regardless of mode).

Post-migration, mural-oauth loses single-step UX — unless the external IdP supports upstream token storage. With a dedicated IdP configured with Mural as a custom social connection (e.g., Auth0's Token Vault), the IdP stores Mural tokens during social login and Banksy retrieves them server-to-server. One step yields both IdP JWT (Layer 1) and Mural tokens (Layer 2). See Non-Enterprise User Gap for spec compliance and limitations.

Without token storage: users authenticate with the external IdP (Layer 1), then separately complete Mural OAuth in the browser (Layer 2) — structurally identical to sso-proxy. The `mural-oauth.ts` token exchange/storage logic persists as the Layer 2 mechanism; Better Auth's MCP plugin is replaced by PRM + external IdP.

| Aspect | Current mural-oauth | Post-migration (without token storage) | Post-migration (with token storage) |
|---|---|---|---|
| Layer 1 (IDE auth) | Better Auth MCP plugin (AS) | External IdP via PRM (RS) | External IdP via PRM (RS) |
| Layer 2 (Mural API) | Mural OAuth (same flow as L1) | Mural OAuth (separate browser step) | IdP retrieves Mural tokens during social login |
| User steps | 1 (Mural consent) | 2 (IdP auth + Mural connect) | 1 (Mural consent via IdP) |
| Token storage | Needed (`muralOauthToken`) | Still needed (Banksy stores) | IdP stores upstream tokens; Banksy retrieves |
| SPA callback | Needed for OAuth callback | Still needed for Layer 2 | Not needed (no separate browser step) |
| `/.well-known` | `oauth-authorization-server` | `oauth-protected-resource` | `oauth-protected-resource` |

The convergence means a single target architecture regardless of starting mode. Same external IdP, same FastMCP auth class, same PRM config. The mural-oauth token exchange/storage becomes the universal Layer 2 mechanism — its use of Mural Public API and standard OAuth tokens makes it the more portable implementation.

### Practical Assessment

**Mural-oauth fixes needed:** Tickets 2 (open redirect) and 6 (plaintext tokens) most urgent. Ticket 1: add single-use nonce. Ticket 7: one-line `history.replaceState()`. Ticket 8: logging cleanup. Ticket 5: no fix needed (atomic upsert sufficient).

**New hardening:** Server-side nonce tracking for state, generic error messages (not `error_description`), scope audit for minimum privilege.

**Migration:** Same target architecture for both modes. Subsumes Ticket 3. Mural-oauth token exchange/storage survives as Layer 2. Fix tickets first — vulnerabilities exist in production now, token encryption persists post-migration, and migration timeline is uncertain.

---

## Outstanding Research

### IDE Support for Protected Resource Metadata (Resolved)

The RS model is the only auth model in the current MCP spec. PR #338 (April 2025) separated MCP servers from authorization servers; the 2025-11-25 revision made it normative: servers MUST implement PRM (RFC 9728), clients MUST use it for AS discovery. No spec-defined path remains for MCP servers as authorization servers. Banksy's current AS model works only because IDEs still attempt legacy discovery as fallback — this will erode.

**IDE support:** All three major clients support PRM today:
- **Cursor** (v1.0+, June 2025): Full PRM discovery, follows `authorization_servers` links, OAuth 2.1 + PKCE. Known bug: `resource_metadata` URL from `WWW-Authenticate` header lost after redirect — only affects non-standard metadata paths, not `/.well-known/oauth-protected-resource`.
- **VS Code / Copilot** (v1.102, July 2025): Full PRM, OIDC Discovery, RFC 8414. Microsoft documents Entra ID integration with RS model.
- **Claude Desktop:** OAuth 2.1 with PRM over streamable HTTP.

**Unsupported clients:** Zed (bug #43162) and Continue.dev (enhancement #6282) lack remote MCP OAuth entirely — can't use Banksy's current AS model either, no regression. Windsurf has no clear remote OAuth PRM support.

**SDKs:** TypeScript MCP SDK (v1.27.1) implements `discoverOAuthProtectedResourceMetadata()` with a known redirect bug (Issue #1234, fix in PR #1350). Python SDK has open PR #982 for AS/RS separation.

**Critical: FastMCP auth classes.** Bare `JWTVerifier` does not serve PRM — IDEs would have no discovery metadata. Must use `RemoteAuthProvider` (DCR-capable IdPs) or `OAuthProxy` (non-DCR IdPs). Both serve PRM and produce spec-compliant resource servers. See External IdP Selection.

**Existing examples:** Microsoft/Entra ID + VS Code, mcp-auth.dev reference implementations, Quarkus tutorial — pattern works end-to-end when serving PRM at standard well-known path.

**Verdict:** RS model viable today for the three major IDEs. Requirements: serve PRM at `/.well-known/oauth-protected-resource`, use `RemoteAuthProvider` or `OAuthProxy` (not bare `JWTVerifier`).

### External IdP Selection

Primary remaining architectural decision. Determines JWT validation config and FastMCP auth class.

**`RemoteAuthProvider`** requires DCR (RFC 7591) — IDEs auto-register with the IdP. Supported by WorkOS AuthKit, Descope, Auth0 (if configured). Composes `JWTVerifier` + automatic PRM endpoints = pure RS with no AS surface.

**`OAuthProxy`** bridges non-DCR IdPs (Google, Azure AD, GitHub) by presenting a DCR interface to IDEs while holding pre-registered upstream credentials. Reintroduces some AS surface (DCR registrations, proxied tokens) but far less than Better Auth, and token validation is still delegated to the IdP.

Layer 2 is unchanged regardless of IdP choice (see What This Changes). The only candidate that could collapse both layers is Mural-as-IdP, assessed below.

**Google:** Already used for sso-proxy Layer 1. ID tokens are JWTs validatable via JWKS, but no custom audiences/scopes (audience = client ID, scopes limited to openid/email/profile). No DCR → requires `OAuthProxy`. Sufficient for "is this a legitimate Google user?" but no fine-grained MCP scope control.

**Mural OAuth:** Investigated as a layer-collapsing candidate (resolves Risk 1 from FastMCP auth strategy plan). Three independent blockers, each individually fatal:

*Blocker 1: HS256 tokens with no JWKS.* Mural OAuth tokens are JWTs signed HS256 with a symmetric secret (`jwt.sign(claims, config.jwt.secret)`). No JWKS endpoint, no asymmetric keys, no issuer/audience claims. Banksy can't validate without Mural's `config.jwt.secret` — a security boundary violation enabling token forgery. Functionally opaque to external validators.

*Blocker 2: No OAuth discovery.* Mural serves no `/.well-known/oauth-authorization-server`, `/.well-known/openid-configuration`, or RFC 8414 metadata. No DCR — clients are pre-registered. IDEs following PRM `authorization_servers` links would find nothing. Neither `RemoteAuthProvider` nor `OAuthProxy` works without a discovery endpoint.

*Blocker 3: MCP token passthrough prohibition.* Even with JWKS and discovery, the spec states: "The MCP server MUST NOT pass through the token it received from the MCP client." Mural-as-IdP does exactly this. RFC 8707 audience binding makes it structural: a token with `resource=https://banksy.example.com` is audience-bound to Banksy (Mural rejects it); a Mural-audience token fails Banksy's validation. No audience satisfies both. The two-layer architecture (IdP JWT for L1, stored Mural tokens for L2) is the spec-compliant pattern.

**Verdict:** Mural-as-IdP is not viable. Blockers 1-2 require Mural infrastructure changes; Blocker 3 is a fundamental MCP constraint. The existence of `mural-oauth` mode doesn't help — `OAuthProxy` delegates to the upstream IdP's JWKS (Mural has none). Mural-oauth's single-step UX depends on Banksy acting as an AS intermediary, which the RS migration eliminates.

**Dedicated IdP** (Auth0, Azure AD/Entra ID, WorkOS, Descope): Full control over JWT shape, JWKS, custom audiences/scopes, token lifetimes. Auth0, WorkOS, Descope support DCR → `RemoteAuthProvider` (pure RS). Azure AD lacks DCR → `OAuthProxy`. Adds infrastructure but eliminates Google's format limitations and enables Banksy-specific scopes (`mcp:tools`, `mural:read`, `mural:write`).

The choice cascades into JWT validation config, scope design, user account linking, and operational burden. DCR + `RemoteAuthProvider` is architecturally cleanest (highest setup cost). Google + `OAuthProxy` is lowest friction (no scope control). But the more fundamental implication: IdP choice determines which Mural user segments can use Banksy at all.

### The Non-Enterprise User Gap

The IdP assessment assumes users have accounts with the external provider. This holds for enterprise SSO and Google users but fails for self-serve Mural users (email/password, individual/team plans).

Mural supports five auth methods: email/password (`api/src/api/session/signin.ts`), Google social (`data/src/data/models/idp/providers/google.ts`), Microsoft social (`data/src/data/models/idp/providers/microsoft.ts`), SAML SSO (`api/src/api/authenticate/saml2/`), OAuth2 SSO (`api/src/api/authenticate/oauth2/authorization/`). The mural-oauth mode reaches all segments via Mural's consent page. The RS migration would regress this.

**Google as IdP:** Excludes email/password users, Microsoft social users, non-Google enterprise SSO users — likely a majority of Mural's user base. Severe regression.

**Dedicated IdP without custom connections:** No Mural user has a pre-existing account. Requires new signup, decouples identity from Mural, creates user-mapping problems.

**Mural-as-IdP:** Covers all users but blocked by the three technical issues above.

This is an access issue. Under RS with Google or a standard dedicated IdP, an entire class of users loses access to Banksy.

#### Resolution Option 1: Dedicated IdP with Mural as a Custom Social Connection

Most promising: a dedicated IdP (Auth0, Descope) configured with Mural as a custom upstream OAuth provider. The IdP provides protocol infrastructure (JWKS, discovery, DCR, RS256 JWTs); Mural handles actual authentication. Auth0 and Descope support custom social connections (manually configure upstream auth/token/userinfo URLs — no upstream discovery needed).

Flow:
1. IDE discovers Banksy's PRM → follows `authorization_servers` to dedicated IdP
2. IdP redirects to Mural (configurable to skip own login page via "Home Realm Discovery") — user sees only Mural's consent page
3. User authenticates with Mural (any method) and authorizes Banksy
4. Mural redirects to IdP with authorization code
5. IdP exchanges code for Mural tokens, fetches user info, issues RS256 JWT
6. IDE presents IdP JWT to Banksy → validates via JWKS

Addresses Blockers 1-2 (IdP issues proper JWTs with JWKS + discovery), sidesteps Blocker 3 (token is IdP-issued, no passthrough). Covers all Mural user segments.

**Token capture for Layer 2.** During step 5, the IdP obtains Mural tokens. Auth0's Token Vault stores these upstream tokens; Banksy retrieves them server-to-server (federated connection access token exchange). If this works for custom social connections (needs PoC), it eliminates the separate "Mural connect" browser step — single-step UX preserved.

**Spec compliance.** The Mural token never comes from the MCP client. IDE sends IdP JWT (audience-bound to Banksy via RFC 8707); Banksy retrieves Mural tokens from IdP's store via server-to-server channel. Identical to current pattern (Banksy stores Mural tokens in Postgres after browser OAuth). The spec's passthrough prohibition targets client-to-upstream forwarding, not this pattern.

**Limitations.** Auth0 Token Vault is Enterprise-only (unpublished pricing, requires sales). Custom Token Exchange in Early Access (2026). Needs PoC: refresh token storage + automatic refresh for custom connections. WorkOS doesn't support custom upstream OAuth (predefined providers only). Descope supports custom OAuth + DCR — evaluate alongside Auth0. Full provider evaluation: `banksy/.cursor/prompts/research-auth-provider-alternatives.md`.

Without token storage, the dedicated IdP still solves user coverage. Layer 2 reverts to separate browser Mural OAuth — minor UX regression (two steps), not an access regression.

#### Resolution Option 2: Mural Evolves Its OAuth Infrastructure

"Build" alternative: Mural's platform team adds RS model infrastructure, enabling Mural as the IdP directly.

*Blocker 1 fix: asymmetric signing + JWKS.* Migrate HS256 → RS256/ES256. Deeply embedded: `jwt.sign(claims, config.jwt.secret)` in `api/src/core/session/tokens/index.ts`, validation hardcoded to `algorithms: ['HS256']` in `api/src/security/jwt/index.ts`. Multiple token types use separate HS256 secrets in `api/config/defaults.json`. Requires: RSA/EC key pair management, update all sign/verify paths, expose JWKS endpoint, handle key rotation, transition period accepting both algorithms.

*Blocker 2 fix: OAuth discovery.* Serve `/.well-known/oauth-authorization-server` or `/.well-known/openid-configuration` (RFC 8414/OIDC). Auth endpoints exist (`api/src/api/authenticate/oauth2/authorization/`) but no metadata document today.

*Blocker 3: persists.* Even with JWKS + discovery, passthrough prohibition prevents using IDE-presented Mural tokens for API calls. Layer 2 still needed. However, RFC 8693 (Token Exchange) would let Banksy exchange the Layer 1 token for separate Layer 2 API tokens server-to-server — preserving single-step UX without passthrough. Mural doesn't implement RFC 8693 today.

*DCR:* Not supported. Would require `OAuthProxy` (workable but adds some AS surface).

Covers all users, no vendor cost. But depends on Mural platform team prioritization — no evidence of JWKS/RS256/discovery work in mural-api. Significant engineering scope, uncertain timeline, outside Banksy team's control.

#### Resolution Option 3: Dual Auth Architecture

Pragmatic fallback: AS model for non-enterprise deployments, RS model for enterprise. Per-instance compliant (each serves one model) but creates two codepaths, two security surfaces, two token validation mechanisms. Ticket 3 only applies to enterprise, leaving AS attack surface active elsewhere.

Sustainable as a transitional state. Not viable long-term: (a) AS model becomes non-compliant as IDEs drop legacy fallbacks, (b) doubles security surface area, (c) audit recommends eliminating AS.

#### Resolution Synthesis

**Recommended:** Dedicated IdP with Mural as custom social connection. Only option that covers all users, is spec-compliant, doesn't depend on Mural platform team, and is implementable today. With upstream token storage: single-step UX. Without: two steps, but no one excluded.

**Long-term ideal:** Mural infrastructure evolution (no vendor dependency) — but uncertain timeline, outside Banksy's control. Pursue as parallel conversation; don't block on it.

**Fallback:** Dual auth as transitional state only, not target architecture.

**Next step:** PoC validating (a) custom social connection flow with Mural upstream, (b) upstream token storage for custom connections, (c) end-to-end with Cursor and VS Code. Provider evaluation (Auth0, Descope, others) with pricing: `banksy/.cursor/prompts/research-auth-provider-alternatives.md`.

---

## Relationship to the FastMCP Migration

Related but separable. "Fix in current flow" items are independent hardening regardless of migration timeline. "FastMCP design direction" (Ticket 3) is migration planning.

Overlap with existing FastMCP auth strategy plan (`fastmcp_auth_strategy_f355d421.plan.md`): Risk 1 (Mural token format) resolved — HS256 JWTs with no JWKS, ruling out Mural-as-IdP. Risks 2 and 4 (`get_access_token()` return value, token refresh lifecycle) remain open. New dimension: `OAuthProxy` vs `RemoteAuthProvider` choice depends on IdP DCR capability. IDE compatibility confirmed (Cursor, VS Code, Claude Desktop). Remaining decision: IdP selection.

---

## Summary of Recommended Sequencing

1. **Now (both modes):** Tickets 1, 2, 4, 6, 7, 8 as backward-compatible hardening. Ticket 5: fix sso-proxy's claim endpoint (mural-oauth's atomic upsert sufficient). Mural-oauth extras: server-side nonce tracking (Ticket 1 completion), generic errors (not `error_description`), scope audit.
2. **Auth provider evaluation + PoC:** IdP must support custom upstream OAuth with Mural (otherwise email/password and non-Google users excluded). Evaluate Auth0, Descope, others (`banksy/.cursor/prompts/research-auth-provider-alternatives.md`) for fit, pricing, complexity. PoC: (a) custom social connection with Mural upstream, (b) upstream token storage for custom connections, (c) end-to-end with Cursor and VS Code. In parallel: Mural platform team conversation re JWKS, discovery, RFC 8693.
3. **IdP decision:** Select provider based on PoC. DCR-capable (Auth0, Descope) → `RemoteAuthProvider`; non-DCR → `OAuthProxy`. Both modes converge on same architecture.
4. **Migration:** Implement FastMCP RS with chosen provider. Mural-oauth token exchange/storage becomes universal Layer 2. With upstream token storage: single-step UX. Validate end-to-end (PRM → IdP auth → token validation → Mural connect → tool invocation) with three major IDEs.
5. **Ticket 3:** Remove Better Auth MCP plugin and `/api/auth/mcp/*` only after replacement is deployed and proven.
