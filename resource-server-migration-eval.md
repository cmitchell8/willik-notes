# Banksy Resource Server Migration: Evaluation and Path Forward

## Table of Contents

- [Executive Summary](#executive-summary)
- [Background](#background)
- [The Resource Server Model](#the-resource-server-model)
  - [What Changes and What Doesn't](#what-changes-and-what-doesnt)
  - [Why Mural Cannot Serve as the IdP](#why-mural-cannot-serve-as-the-idp)
  - [Mode Convergence](#mode-convergence)
- [External IdP Selection](#external-idp-selection)
  - [The User Coverage Constraint](#the-user-coverage-constraint)
  - [Candidate Assessment](#candidate-assessment)
  - [Recommended Path: Dedicated IdP with Mural as Custom Social Connection](#recommended-path-dedicated-idp-with-mural-as-custom-social-connection)
  - [Token Capture for Layer 2](#token-capture-for-layer-2)
  - [FastMCP Auth Class Selection](#fastmcp-auth-class-selection)
- [IDE Compatibility](#ide-compatibility)
- [Security Hardening (Pre-Migration)](#security-hardening-pre-migration)
  - [Ticket Landscape](#ticket-landscape)
  - [Key Vulnerabilities to Fix Now](#key-vulnerabilities-to-fix-now)
  - [Mural-OAuth-Specific Hardening](#mural-oauth-specific-hardening)
  - [Migration Ticket](#migration-ticket)
- [Recommended Sequencing](#recommended-sequencing)
- [Relationship to FastMCP Migration](#relationship-to-fastmcp-migration)
- [Appendices](#appendices)
  - [Appendix A: Ticket-by-Ticket Assessment](#appendix-a-ticket-by-ticket-assessment)
  - [Appendix B: IDE Client Support Details](#appendix-b-ide-client-support-details)
  - [Appendix C: Mural-as-IdP Detailed Blocker Analysis](#appendix-c-mural-as-idp-detailed-blocker-analysis)
  - [Appendix D: Mural-OAuth Security Concerns Beyond Audit](#appendix-d-mural-oauth-security-concerns-beyond-audit)
  - [Appendix E: Mural Infrastructure Evolution — Per-Blocker Resolution](#appendix-e-mural-infrastructure-evolution--per-blocker-resolution)

## Executive Summary

Banksy must migrate from OAuth Authorization Server to Resource Server — a requirement driven by MCP specification compliance and the elimination of unnecessary security attack surface. The migration affects only Layer 1 (IDE → Banksy authentication); Layer 2 (Banksy → Mural API access) is unchanged. Mural cannot serve as the external IdP due to three independent blockers: HS256 tokens without JWKS, no OAuth discovery metadata, and the MCP token passthrough prohibition. The recommended path is a dedicated IdP (Auth0 or Descope) with Mural configured as a custom social connection, which covers all Mural user segments and preserves single-step UX if the IdP supports upstream token storage. Both auth modes (sso-proxy and mural-oauth) converge to the same target architecture under RS, making mode divergence a transitional artifact. Seven of eight audit tickets are backward-compatible security hardening that should ship now, independent of migration timeline. The primary remaining decision is IdP selection, pending a PoC that validates custom social connection flow and upstream token storage.

---

## Background

Banksy is the MCP server that connects AI-powered IDEs to Mural's collaboration platform. It authenticates IDE users, then executes tool calls against Mural's API on their behalf. Authentication operates across two stacked OAuth-like layers:

- **Layer 1 (IDE → Banksy):** Establishes user identity. Today, Banksy operates as an OAuth Authorization Server (AS) — Better Auth's MCP plugin serves `/.well-known/oauth-authorization-server`, handles Dynamic Client Registration (DCR), and issues MCP tokens.
- **Layer 2 (Banksy → Mural):** Provides API access. Banksy stores Mural OAuth tokens (access + refresh) and uses them server-side to execute tool calls against Mural's API.

Banksy supports three auth modes (configured via `AUTH_MODE`, one per deployment):

- **sso-proxy:** Layer 1 uses Google OAuth via an SSO proxy. Layer 2 uses a session-activation code/nonce pattern where the browser performs Mural OAuth and Banksy claims the tokens.
- **mural-oauth:** Layer 1 redirects to Mural's consent page — Mural serves as both IdP and API token source, collapsing two layers into one user-facing flow. Layer 2 is embedded: the authorization code grant with Mural yields identity (via `/api/public/v1/users/me`) and API tokens stored for Mural API calls.
- **m2m:** Machine-to-machine, out of scope for this analysis.

A security audit in early 2026 produced eight tickets (critical, high, medium) targeting sso-proxy and a design preamble recommending Banksy adopt an OAuth Resource Server posture under FastMCP. The mural-oauth mode was introduced after the audit; this analysis covers both modes.

The audit’s central recommendation is that Banksy stop operating its own MCP OAuth authorization server surface and instead adopt a resource-server posture under FastMCP. The MCP authorization specification defines MCP servers as OAuth resource servers and requires them to publish Protected Resource Metadata (PRM), which MCP clients use to discover the authorization server. [reference:MCP-Roles](https://modelcontextprotocol.io/specification/latest/basic/authorization#roles) This document evaluates that migration: what it means, what it costs, what decisions remain, and what risks exist.

---

## The Resource Server Model

The audit recommends Banksy stop operating as an OAuth **Authorization Server** (AS) — issuing tokens — and instead become a **Resource Server** (RS) that validates tokens issued by an external Identity Provider (IdP). ("Resource server" is OAuth 2.0 terminology, unrelated to MCP Resources.)

### What Changes and What Doesn't

The RS migration replaces Layer 1 only:

- **Removed:** Better Auth's MCP plugin, `/.well-known/oauth-authorization-server`, `/api/auth/mcp/*`, DCR registration handling, token issuance.
- **Added:** `/.well-known/oauth-protected-resource` (RFC 9728) pointing IDEs to an external IdP. JWT validation (signature via JWKS, issuer, audience, expiration). Scope-based authorization.
- **Unchanged:** Layer 2. Banksy still stores and uses Mural API tokens. The IDE-presented JWT answers "is this a legitimate user?" but doesn't provide Mural tokens.

The rationale is both security and compliance. Running an AS exposes confused-deputy attacks, DCR abuse, and authorization code interception; eliminating the AS eliminates those surfaces. The MCP authorization specification requires servers to implement OAuth 2.0 Protected Resource Metadata (RFC 9728), which MCP clients must use to discover the authorization server.[reference:MCP-overview](https://modelcontextprotocol.io/specification/latest/basic/authorization#overview) Under the MCP authorization model, the MCP server is defined as the OAuth resource server and must publish Protected Resource Metadata so clients can discover the appropriate authorization server. The authorization server may still be hosted alongside the resource server, but it remains a logically separate OAuth role. Banksy’s current authorization-server implementation may still function in some MCP clients because of existing client behavior or compatibility paths, but the specification defines PRM-based authorization server discovery as the standard mechanism going forward.[referece-MCP Authorization Server Discovery](https://modelcontextprotocol.io/specification/latest/basic/authorization#authorization-server-discovery)

Post-migration flow (both modes):

1. **One-time setup (browser):** User authenticates with external IdP (Layer 1), completes Mural OAuth (Layer 2). Banksy stores Mural tokens.
2. **Every MCP request (IDE):** IDE presents an IdP-issued JWT. Banksy validates it, looks up Mural tokens in Postgres, executes the tool call, returns the result.

### Why Mural Cannot Serve as the IdP

The only IdP candidate that could collapse both layers is Mural itself. Three independent blockers, each individually fatal, prevent this:

1. **HS256 tokens with no JWKS.** Mural OAuth tokens are JWTs signed with a symmetric secret (HS256). No JWKS endpoint, no asymmetric keys, no issuer/audience claims. Banksy cannot validate them without possessing Mural's signing secret — a security boundary violation.
2. **No OAuth discovery.** Mural serves no `/.well-known/oauth-authorization-server`, `/.well-known/openid-configuration`, or RFC 8414 metadata. No DCR. IDEs following PRM `authorization_servers` links would find nothing.
3. **MCP token passthrough prohibition.** The MCP spec states: "The MCP server MUST NOT pass through the token it received from the MCP client." RFC 8707 audience binding makes this structural: a token audience-bound to Banksy is rejected by Mural; a Mural-audience token fails Banksy's validation. The two-layer architecture (IdP JWT for L1, stored Mural tokens for L2) is the spec-compliant pattern.

Blockers 1-2 could theoretically be resolved by Mural platform changes (see [Appendix E](#appendix-e-mural-infrastructure-evolution--per-blocker-resolution)). Blocker 3 is a fundamental MCP constraint. The two-layer separation is inherent, not a consequence of IdP choice. See [Appendix C](#appendix-c-mural-as-idp-detailed-blocker-analysis) for code-level evidence.

### Mode Convergence

The RS migration causes mural-oauth and sso-proxy to converge to the same target architecture — the most significant structural finding of this analysis.

Today mural-oauth provides single-step UX: one Mural consent yields identity + API access. Under RS, Layer 1 requires an external IdP with JWKS-validatable tokens. Mural doesn't qualify (three blockers above apply regardless of mode). Post-migration, mural-oauth loses single-step UX — unless the external IdP supports upstream token storage (see [Token Capture for Layer 2](#token-capture-for-layer-2)).

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

---

## External IdP Selection

IdP selection is the primary remaining architectural decision. It determines JWT validation configuration, FastMCP auth class, and — critically — which Mural user segments can use Banksy at all.

### The User Coverage Constraint

The IdP assessment assumes users have accounts with the external provider. This holds for enterprise SSO and Google users but fails for self-serve Mural users (email/password, individual/team plans). Mural supports five auth methods: email/password (`api/src/api/session/signin.ts`), Google social (`data/src/data/models/idp/providers/google.ts`), Microsoft social (`data/src/data/models/idp/providers/microsoft.ts`), SAML SSO (`api/src/api/authenticate/saml2/`), OAuth2 SSO (`api/src/api/authenticate/oauth2/authorization/`). The mural-oauth mode reaches all segments via Mural's consent page. The RS migration would regress this.

Google as IdP excludes email/password users, Microsoft social users, and non-Google enterprise SSO users — likely a majority of Mural's user base. A dedicated IdP without custom connections requires new signups decoupled from Mural identity, creating user-mapping problems. Mural-as-IdP covers all users but is blocked (see [Why Mural Cannot Serve as the IdP](#why-mural-cannot-serve-as-the-idp)). This is an access issue: under RS with Google or a standard dedicated IdP, an entire class of users loses access to Banksy.

### Candidate Assessment

**Google:** Already used for sso-proxy Layer 1. ID tokens are JWTs validatable via JWKS, but no custom audiences/scopes (audience = client ID, scopes limited to openid/email/profile). No DCR → requires `OAuthProxy`. Sufficient for "is this a legitimate Google user?" but no fine-grained MCP scope control. Severe user coverage regression.

**Dedicated IdP** (Auth0, Azure AD/Entra ID, WorkOS, Descope): Full control over JWT shape, JWKS, custom audiences/scopes, token lifetimes. Auth0, WorkOS, Descope support DCR → `RemoteAuthProvider` (pure RS). Azure AD lacks DCR → `OAuthProxy`. Adds infrastructure but eliminates Google's format limitations and enables Banksy-specific scopes (`mcp:tools`, `mural:read`, `mural:write`).

**Mural infrastructure evolution:** Mural could eliminate the need for an external IdP by adding JWKS, discovery, and optionally RFC 8693 (Token Exchange) — significant effort, uncertain timeline, outside Banksy team's control. No evidence of this work in mural-api. Viable as a long-term ideal but not a near-term option. See [Appendix E](#appendix-e-mural-infrastructure-evolution--per-blocker-resolution) for per-blocker resolution details.

**Dual auth (fallback):** AS model for non-enterprise deployments, RS model for enterprise. Per-instance compliant (each serves one model) but Ticket 3 only applies to enterprise, leaving the AS attack surface active elsewhere. Sustainable only as a transitional state — AS becomes non-compliant as IDEs drop legacy fallbacks, doubles security surface area, and contradicts the audit recommendation.

The choice cascades into JWT validation config, scope design, user account linking, and operational burden. But the most fundamental implication is user coverage: IdP choice determines which Mural user segments can use Banksy at all.

### Recommended Path: Dedicated IdP with Mural as Custom Social Connection

A dedicated IdP (Auth0, Descope) configured with Mural as a custom upstream OAuth provider. The IdP provides protocol infrastructure (JWKS, discovery, DCR, RS256 JWTs); Mural handles actual authentication. Auth0 and Descope support custom social connections (manually configure upstream auth/token/userinfo URLs — no upstream discovery needed).

Flow:
1. IDE discovers Banksy's PRM → follows `authorization_servers` to dedicated IdP
2. IdP redirects to Mural (configurable to skip own login page via "Home Realm Discovery") — user sees only Mural's consent page
3. User authenticates with Mural (any method) and authorizes Banksy
4. Mural redirects to IdP with authorization code
5. IdP exchanges code for Mural tokens, fetches user info, issues RS256 JWT
6. IDE presents IdP JWT to Banksy → validates via JWKS

Addresses Blockers 1-2 (IdP issues proper JWTs with JWKS + discovery), sidesteps Blocker 3 (token is IdP-issued, no passthrough). Covers all Mural user segments. This is the only option that covers all users, is spec-compliant, doesn't depend on Mural platform team, and is implementable today.

With upstream token storage: single-step UX. Without: two steps, but no one excluded.

**Long-term ideal:** Mural infrastructure evolution (no vendor dependency) — but uncertain timeline, outside Banksy's control. Pursue as parallel conversation; don't block on it.

**Next step:** PoC validating (a) custom social connection flow with Mural upstream, (b) upstream token storage for custom connections, (c) end-to-end with Cursor and VS Code. Provider evaluation (Auth0, Descope, others) with pricing: `banksy/.cursor/prompts/research-auth-provider-alternatives.md`.

### Token Capture for Layer 2

During step 5 of the recommended flow, the IdP obtains Mural tokens. Auth0's Token Vault stores these upstream tokens; Banksy retrieves them server-to-server (federated connection access token exchange). If this works for custom social connections (needs PoC), it eliminates the separate "Mural connect" browser step — single-step UX preserved.

The Mural token never comes from the MCP client. The IDE sends an IdP JWT (audience-bound to Banksy via RFC 8707); Banksy retrieves Mural tokens from the IdP's store via a server-to-server channel. Identical in pattern to the current approach (Banksy stores Mural tokens in Postgres after browser OAuth). The spec's passthrough prohibition targets client-to-upstream forwarding, not this pattern.

Limitations: Auth0 Token Vault is Enterprise-only (unpublished pricing, requires sales). Custom Token Exchange in Early Access (2026). Needs PoC: refresh token storage + automatic refresh for custom connections. WorkOS doesn't support custom upstream OAuth (predefined providers only). Descope supports custom OAuth + DCR — evaluate alongside Auth0. Full provider evaluation: `banksy/.cursor/prompts/research-auth-provider-alternatives.md`.

Without token storage, the dedicated IdP still solves user coverage. Layer 2 reverts to separate browser Mural OAuth — minor UX regression (two steps), not an access regression.

### FastMCP Auth Class Selection

The choice of external IdP determines which FastMCP auth class Banksy uses:

**`RemoteAuthProvider`** If the chosen authorization server supports OAuth Dynamic Client Registration (RFC 7591), MCP clients can automatically register. If the authorization server does not support DCR, a proxy or pre-registered client configuration may be required. [reference-MCP Dynamic Client Registration](https://modelcontextprotocol.io/specification/latest/basic/authorization#dynamic-client-registration) Supported by WorkOS AuthKit, Descope, Auth0 (if configured). Composes `JWTVerifier` + automatic PRM endpoints = pure RS with no AS surface. Architecturally cleanest.

**`OAuthProxy`** bridges non-DCR IdPs (Google, Azure AD, GitHub) by presenting a DCR interface to IDEs while holding pre-registered upstream credentials. Reintroduces some AS surface (DCR registrations, proxied tokens) but far less than Better Auth, and token validation is still delegated to the IdP.

A JWT verification layer alone is insufficient for MCP interoperability unless the server also serves Protected Resource Metadata (PRM), because MCP clients rely on PRM to discover authorization servers.[reference:MCP overview](https://modelcontextprotocol.io/specification/latest/basic/authorization#overview) Must use one of the two classes above. Both serve PRM and produce spec-compliant resource servers. DCR + `RemoteAuthProvider` is architecturally cleanest (highest setup cost). Google + `OAuthProxy` is lowest friction (no scope control).

---

## IDE Compatibility

The RS model is viable today for the three major IDEs. Cursor (v1.0+), VS Code/Copilot (v1.102+), and Claude Desktop all support Protected Resource Metadata discovery and OAuth 2.1 with PKCE. Requirements: serve PRM at `/.well-known/oauth-protected-resource`, use `RemoteAuthProvider` or `OAuthProxy` (not bare `JWTVerifier`).

Zed and Continue.dev lack remote MCP OAuth entirely — they cannot use Banksy's current AS model either, so the migration causes no regression. Windsurf has no clear remote OAuth PRM support.

Per-client details (Cursor redirect bug, SDK status, version specifics) are in [Appendix B](#appendix-b-ide-client-support-details).

---

## Security Hardening (Pre-Migration)

### Ticket Landscape

The audit produced eight tickets. Seven have "fix in current flow" recommendations that are backward-compatible and safe to ship independently of the RS migration. The eighth (Ticket 3) is the migration itself — remove Better Auth's MCP plugin and the AS surface.

Six of eight tickets apply to mural-oauth (introduced after the audit) with varying severity: four apply directly with the same fix, one is substantially mitigated, and one has reduced applicability. Ticket 3 applies equally — same plugin, removed from both modes simultaneously.

### Key Vulnerabilities to Fix Now

**Open redirect (Ticket 2, Critical):** `url.startsWith('/')` accepts protocol-relative URLs like `//evil.com`. Present in both modes (sso-proxy and mural-oauth's `isValidCallbackUrl`). Fix: parse with `new URL(url, baseOrigin)`, require `u.origin === baseOrigin`.

**Plaintext refresh tokens (Ticket 6, High):** Mural tokens stored unencrypted in Postgres (`muralSessionToken` and `muralOauthToken`). Persists post-migration — this fix is needed regardless of timeline. Fix: envelope encryption with Azure Key Vault (transparent encrypt-on-write, decrypt-on-read). Shared abstraction in `mural-tokens.ts` means one fix covers both tables.

**OAuth login CSRF (Ticket 1, Critical):** sso-proxy's Google OAuth callback logs a warning on state validation failure but continues to create a session. Mural-oauth is substantially stronger (HMAC-SHA256 state with `crypto.timingSafeEqual` and 10-minute TTL) but state is not single-use. Fix for sso-proxy: reject on mismatch. Fix for mural-oauth: server-side nonce tracking for single-use enforcement.

**Security headers (Ticket 4, High):** Auth pages lack CSP, HSTS, Referrer-Policy, anti-framing headers. Purely additive (Azure Front Door or middleware). Applies equally to both modes.

**OAuth codes in URL history (Ticket 7, Medium):** SPA doesn't call `history.replaceState()` after reading the authorization code. One-line fix in both modes.

**Sensitive auth logging (Ticket 8, Medium):** Replace truncated state/code values with correlation IDs. Mural-oauth: stop logging full `callbackURL`, stop passing `error_description` to client.

**Mural claim race condition (Ticket 5, High):** Applies to sso-proxy's `/auth/mural/claim` only. Mural-oauth's atomic `INSERT ... ON CONFLICT DO UPDATE` is sufficient.

### Mural-OAuth-Specific Hardening

Beyond the audit tickets, mural-oauth introduces concerns requiring additional hardening: a 10-minute state replay window (fix with server-side nonce tracking), SPA error parameter reflection (fix with generic error messages), and overly broad default scopes (audit for minimum privilege). Full details in [Appendix D](#appendix-d-mural-oauth-security-concerns-beyond-audit).

### Migration Ticket

**Ticket 3 (High) — Remove Legacy MCP OAuth Surface:** Remove Better Auth's MCP plugin, `/api/auth/mcp/*`, and `/.well-known/oauth-authorization-server`. Execute only after the RS replacement is deployed and tested. Applies equally to both modes.

Full ticket-by-ticket detail (both modes, with mural-oauth extrapolations) is in [Appendix A](#appendix-a-ticket-by-ticket-assessment).

---

## Recommended Sequencing

1. **Now (both modes):** Tickets 1, 2, 4, 6, 7, 8 as backward-compatible hardening. Ticket 5: fix sso-proxy's claim endpoint (mural-oauth's atomic upsert sufficient). Mural-oauth extras: server-side nonce tracking (Ticket 1 completion), generic errors (not `error_description`), scope audit.
2. **Auth provider evaluation + PoC:** IdP must support custom upstream OAuth with Mural (otherwise email/password and non-Google users excluded). Evaluate Auth0, Descope, others (`banksy/.cursor/prompts/research-auth-provider-alternatives.md`) for fit, pricing, complexity. PoC: (a) custom social connection with Mural upstream, (b) upstream token storage for custom connections, (c) end-to-end with Cursor and VS Code. In parallel: Mural platform team conversation re JWKS, discovery, RFC 8693.
3. **IdP decision:** Select provider based on PoC. DCR-capable (Auth0, Descope) → `RemoteAuthProvider`; non-DCR → `OAuthProxy`. Both modes converge on same architecture.
4. **Migration:** Implement FastMCP RS with chosen provider. Mural-oauth token exchange/storage becomes universal Layer 2. With upstream token storage: single-step UX. Validate end-to-end (PRM → IdP auth → token validation → Mural connect → tool invocation) with three major IDEs.
5. **Ticket 3:** Remove Better Auth MCP plugin and `/api/auth/mcp/*` only after replacement is deployed and proven.

---

## Relationship to FastMCP Migration

The RS migration and the FastMCP migration are related but separable. Security hardening (the seven backward-compatible tickets) is independent work regardless of migration timeline. Ticket 3 is migration planning.

Overlap with existing FastMCP auth strategy plan (`fastmcp_auth_strategy_f355d421.plan.md`): Risk 1 (Mural token format) is resolved — HS256 JWTs with no JWKS, ruling out Mural-as-IdP. Risks 2 and 4 (`get_access_token()` return value, token refresh lifecycle) remain open. The `OAuthProxy` vs `RemoteAuthProvider` choice is a new dimension that depends on IdP DCR capability. IDE compatibility is confirmed (Cursor, VS Code, Claude Desktop). The remaining decision is IdP selection.

---

## Appendices

### Appendix A: Ticket-by-Ticket Assessment

#### sso-proxy Mode

**Ticket 1 (Critical) — OAuth Login CSRF:** sso-proxy's Google OAuth callback logs a warning on state validation failure but continues to create a session. Fix: generate crypto-secure state server-side, store with TTL, reject on mismatch/expiration, enforce single-use.

**Ticket 2 (Critical) — Open Redirect:** `url.startsWith('/')` accepts protocol-relative URLs like `//evil.com`. Fix: parse with `new URL(url, baseOrigin)`, require `u.origin === baseOrigin`.

**Ticket 3 (High) — Remove Legacy MCP OAuth Surface:** The only ticket that breaks existing behavior — remove Better Auth's MCP plugin, `/api/auth/mcp/*`, and `/.well-known/oauth-authorization-server`. This is the migration itself, scoped as a ticket. IDE compatibility confirmed for Cursor, VS Code, Claude Desktop. Prerequisites: select external IdP, implement replacement via `RemoteAuthProvider` or `OAuthProxy`. Execute only after replacement is deployed and tested.

**Ticket 4 (High) — Security Headers:** Auth pages lack CSP, HSTS, Referrer-Policy, anti-framing headers. Purely additive (Azure Front Door or middleware).

**Ticket 5 (High) — Mural Claim Race Condition:** `/auth/mural/claim` reads, claims, saves, deletes in non-atomic steps — concurrent claims can both succeed. Fix: atomic compare-and-set with `claimedAt`/`status` column.

**Ticket 6 (High) — Plaintext Refresh Tokens:** Mural tokens stored unencrypted in Postgres. Fix: envelope encryption with Azure Key Vault (transparent encrypt-on-write, decrypt-on-read).

**Ticket 7 (Medium) — OAuth Codes in URL History:** SPA doesn't call `history.replaceState()` after reading the authorization code. One-line fix.

**Ticket 8 (Medium) — Sensitive Auth Logging:** Auth flows log truncated state/code values. Fix: replace with correlation IDs.

#### mural-oauth Mode Extrapolation

Six of eight tickets apply to mural-oauth with varying severity.

**Direct apply (same fix):** Ticket 2 (open redirect — identical `startsWith('/')` bug in `isValidCallbackUrl`), Ticket 4 (security headers — same infrastructure, same gap), Ticket 6 (plaintext tokens in `muralOauthToken` — persists post-migration; shared abstraction in `mural-tokens.ts` means one fix covers both tables), Ticket 7 (code not scrubbed from history in `oauth-callback.tsx`).

**Partially addressed:** Ticket 1 (CSRF) — substantially mitigated via HMAC-SHA256 state with nonce, timestamp, 10-minute TTL, and `crypto.timingSafeEqual`. Materially stronger than sso-proxy's "log and continue." Gap: state is not single-use (replayable within TTL). Mitigated by Mural's single-use codes; defense-in-depth calls for server-side nonce tracking.

**Reduced applicability:** Ticket 5 — no `/auth/mural/claim` endpoint; token storage uses atomic `INSERT ... ON CONFLICT DO UPDATE`. Ticket 8 — doesn't log tokens/codes but logs `callbackURL` and passes `error_description` to client; clean up both.

**Identical:** Ticket 3 applies equally — same Better Auth MCP plugin, removed from both modes simultaneously.

#### mural-oauth Priority Assessment

**Most urgent:** Tickets 2 (open redirect) and 6 (plaintext tokens). **Next:** Ticket 1 (add single-use nonce). **Quick wins:** Ticket 7 (one-line `history.replaceState()`). **Cleanup:** Ticket 8 (logging). **No fix needed:** Ticket 5 (atomic upsert sufficient).

---

### Appendix B: IDE Client Support Details

The RS model is the only auth model in the current MCP spec. PR #338 (April 2025) separated MCP servers from authorization servers; the 2025-11-25 revision made it normative: servers MUST implement PRM (RFC 9728), clients MUST use it for AS discovery. As defined in the MCP authorization model, the MCP server acts as the OAuth resource server and publishes Protected Resource Metadata (PRM) so clients can discover the appropriate authorization server.

**Cursor** (v1.0+, June 2025): Full PRM discovery, follows `authorization_servers` links, OAuth 2.1 + PKCE. Known bug: `resource_metadata` URL from `WWW-Authenticate` header lost after redirect — only affects non-standard metadata paths, not `/.well-known/oauth-protected-resource`.

**VS Code / Copilot** (v1.102, July 2025): Full PRM, OIDC Discovery, RFC 8414. Microsoft documents Entra ID integration with RS model.

**Claude Desktop:** OAuth 2.1 with PRM over streamable HTTP.

**Zed:** Bug #43162 — lacks remote MCP OAuth entirely. Cannot use Banksy's current AS model either; no regression from RS migration.

**Continue.dev:** Enhancement #6282 — lacks remote MCP OAuth entirely. Same assessment as Zed.

**Windsurf:** No clear remote OAuth PRM support.

**TypeScript MCP SDK** (v1.27.1): Implements `discoverOAuthProtectedResourceMetadata()` with a known redirect bug (Issue #1234, fix in PR #1350).

**Python MCP SDK:** Open PR #982 for AS/RS separation.

**Existing end-to-end examples:** Microsoft/Entra ID + VS Code, mcp-auth.dev reference implementations, Quarkus tutorial — pattern works end-to-end when serving PRM at standard well-known path.

---

### Appendix C: Mural-as-IdP Detailed Blocker Analysis

#### Blocker 1: HS256 Tokens with No JWKS

Mural OAuth tokens are JWTs signed HS256 with a symmetric secret (`jwt.sign(claims, config.jwt.secret)` in `api/src/core/session/tokens/index.ts`). Validation is hardcoded to `algorithms: ['HS256']` in `api/src/security/jwt/index.ts`. No JWKS endpoint exists. No asymmetric keys. No issuer or audience claims in the token payload.

Banksy cannot validate these tokens without possessing Mural's `config.jwt.secret` — a security boundary violation that would enable token forgery. The tokens are functionally opaque to any external validator (resolves Risk 1 from the FastMCP auth strategy plan).

#### Blocker 2: No OAuth Discovery

Mural serves no `/.well-known/oauth-authorization-server`, `/.well-known/openid-configuration`, or RFC 8414 metadata. DCR is not supported — clients are pre-registered. IDEs following PRM `authorization_servers` links would find nothing. Neither `RemoteAuthProvider` nor `OAuthProxy` works without a discovery endpoint.

Auth endpoints exist (`api/src/api/authenticate/oauth2/authorization/`) but no metadata document aggregates them.

#### Blocker 3: MCP Token Passthrough Prohibition

The MCP spec states: "The MCP server MUST NOT pass through the token it received from the MCP client." [reference MCP-TokenPassthrough](https://modelcontextprotocol.io/docs/security-best-practices#token-passthrough) Using Mural-as-IdP does exactly this. RFC 8707 audience binding makes the prohibition structural:

- A token with `resource=https://banksy.example.com` is audience-bound to Banksy — Mural rejects it.
- A Mural-audience token fails Banksy's validation.
- No audience value satisfies both.

The two-layer architecture (IdP JWT for L1, stored Mural tokens for L2) is the spec-compliant pattern. This blocker persists even if Mural resolves Blockers 1-2.

#### Mural-OAuth Mode Implications

The existence of `mural-oauth` mode doesn't help bypass these blockers. `OAuthProxy` delegates to the upstream IdP's JWKS (Mural has none). Mural-oauth's single-step UX depends on Banksy acting as an AS intermediary, which the RS migration eliminates.

---

### Appendix D: Mural-OAuth Security Concerns Beyond Audit

Concerns introduced by the mural-oauth mode beyond the eight audit tickets:

**State replay window.** HMAC-signed state is replayable for 10 minutes — no server-side nonce storage or consumption tracking. Mural's single-use authorization codes limit practical impact, but the window is wider than necessary. Fix: store used nonces in Redis/DB with TTL matching `STATE_MAX_AGE_MS`, reject seen nonces.

**Better Auth secret as CSRF root of trust.** State HMAC is keyed with `ctx.context.secret`. Compromise of this secret enables forging valid state for arbitrary callbacks (login CSRF). Not a vulnerability per se — any HMAC scheme depends on secret integrity — but makes this secret a higher-value target than in sso-proxy mode.

**SPA error parameter reflection.** `oauth-callback.tsx` renders `error` and `error_description` from URL params. React's text escaping prevents XSS when rendered as text, but unsanitized values risk phishing if ever rendered as HTML. Fix: generic error to user, log `error_description` server-side only.

**Scope breadth.** Default scopes (`murals:read`, `murals:write`, `workspaces:read`, `rooms:read`, `identity:read`, `templates:read`) are broad. Overridable via `MURAL_OAUTH_SCOPES` but no runtime validation against tool requirements. Audit for minimum privilege.

**Client credentials in environment.** `MURAL_OAUTH_CLIENT_ID` and `MURAL_OAUTH_CLIENT_SECRET` as env vars. Handled appropriately (secret used only for token exchange and HMAC, client ID truncated in logs). Should be rotated periodically and stored in Azure Key Vault for production.

---

### Appendix E: Mural Infrastructure Evolution — Per-Blocker Resolution

"Build" alternative: Mural's platform team adds RS model infrastructure, enabling Mural as the IdP directly.

#### Blocker 1 Fix: Asymmetric Signing + JWKS

Migrate HS256 → RS256/ES256. The current implementation is deeply embedded:

- `jwt.sign(claims, config.jwt.secret)` in `api/src/core/session/tokens/index.ts`
- Validation hardcoded to `algorithms: ['HS256']` in `api/src/security/jwt/index.ts`
- Multiple token types use separate HS256 secrets in `api/config/defaults.json`

Requires: RSA/EC key pair management, update all sign/verify paths, expose JWKS endpoint, handle key rotation, transition period accepting both algorithms.

#### Blocker 2 Fix: OAuth Discovery

Serve `/.well-known/oauth-authorization-server` or `/.well-known/openid-configuration` (RFC 8414/OIDC). Auth endpoints exist (`api/src/api/authenticate/oauth2/authorization/`) but no metadata document aggregates them today.

#### Blocker 3: Persists

Even with JWKS + discovery, the passthrough prohibition prevents using IDE-presented Mural tokens for API calls. Layer 2 is still needed. However, RFC 8693 (Token Exchange) would let Banksy exchange the Layer 1 token for separate Layer 2 API tokens server-to-server — preserving single-step UX without passthrough. Mural doesn't implement RFC 8693 today.

#### DCR

Not supported by Mural. Would require `OAuthProxy` (workable but adds some AS surface).

#### Assessment

Covers all users, no vendor cost. But depends on Mural platform team prioritization — no evidence of JWKS/RS256/discovery work in mural-api. Significant engineering scope, uncertain timeline, outside Banksy team's control. Viable as a long-term ideal; pursue as a parallel conversation without blocking near-term migration on it.
