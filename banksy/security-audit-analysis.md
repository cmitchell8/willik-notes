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

In early 2026, a security audit was conducted on Banksy with knowledge that a migration to FastMCP (Python) was under consideration. The audit produced eight tickets spanning critical, high, and medium severity, along with a design preamble recommending that Banksy adopt an OAuth Resource Server posture under FastMCP. This document records our analysis of those findings — what's straightforward to act on, what raises deeper architectural questions, and what research has been completed to inform the migration direction.

Banksy supports multiple auth modes (`sso-proxy`, `mural-oauth`, `m2m`), configured via the `AUTH_MODE` environment variable. Only one mode is active per deployment. The audit was conducted against the `sso-proxy` mode — the only mode that existed at the time — so none of its findings account for the `mural-oauth` mode introduced afterward. This document covers both modes: the original audit analysis for sso-proxy and an extrapolation of those findings to mural-oauth, along with new security concerns and migration implications specific to the newer mode.

Banksy's auth system is effectively two stacked OAuth-like layers, though the mechanics differ by mode:

- **Layer 1 (IDE → Banksy):** In sso-proxy mode, MCP OAuth plus Google OAuth (via SSO proxy) establishes a Banksy user session and issues MCP tokens for IDE access. In mural-oauth mode, the MCP OAuth flow redirects to Mural's own OAuth consent page, and Mural serves as both the identity provider and the source of API tokens — collapsing what are architecturally two layers into a single user-facing flow.
- **Layer 2 (Banksy → Mural):** In sso-proxy mode, a session-activation code/nonce pattern enables the browser to perform Mural IdP OAuth and activate a server-initiated pending session, which the Banksy server later claims (code + server-only nonce) to obtain Mural JWT and refresh tokens for server-side use. In mural-oauth mode, Layer 2 is embedded in the same OAuth flow as Layer 1 — the authorization code grant with Mural yields both identity information (via the `/api/public/v1/users/me` endpoint) and API tokens (access + refresh) that Banksy stores for subsequent Mural API calls.

The audit's recommendations divide cleanly into two categories per ticket: "fix in current flow" items that harden the existing architecture without changing it, and "FastMCP design direction" items that assume the migration has happened. Understanding which is which matters, because they carry very different risk profiles and timelines.

---

## The Audit's Central Recommendation: Banksy as a Resource Server

The audit preamble recommends that Banksy stop operating as an OAuth **Authorization Server** (AS) — the component that issues tokens — and instead become a **Resource Server** (RS) that validates tokens issued by an external Identity Provider. (Note: "resource server" here is OAuth 2.0 terminology, unrelated to MCP Resources.)

Banksy is an AS today: Better Auth's MCP plugin serves `/.well-known/oauth-authorization-server`, handles DCR, and issues MCP tokens. Under the RS model, Banksy would instead serve `/.well-known/oauth-protected-resource` (RFC 9728, Protected Resource Metadata) pointing IDE clients to an external IdP. The IDE would authenticate with the IdP, receive a JWT, and present it to Banksy on every MCP request. Banksy would validate the JWT's signature (via the IdP's JWKS), issuer, audience, and expiration, then authorize tool calls based on claims and scopes.

The rationale is sound: running an Authorization Server exposes you to confused-deputy attacks, DCR abuse, authorization code interception, and other high-risk attack classes. If you can avoid being an AS entirely, those attack surfaces disappear. The audit's Ticket 3 is the concrete expression of this — remove the Better Auth MCP plugin and the `/api/auth/mcp/*` endpoints.

### What This Changes and What It Doesn't

The resource server model only affects Layer 1 — how the IDE authenticates MCP requests to Banksy. Layer 2 is unchanged: Banksy still stores Mural API tokens and uses them for Mural API calls. The IDE-presented JWT answers "is this a legitimate user?" but does not give Banksy the Mural tokens it needs.

This two-layer separation is inherent to the architecture, not a consequence of IdP choice. The only way to eliminate Layer 2 would be to use Mural itself as the IdP — but investigation confirmed this is not viable (see the three blockers in External IdP Selection). Layer 2 persists regardless of which IdP is chosen.

This conclusion applies to mural-oauth as well. Despite that mode combining both layers into a single Mural OAuth flow today, the resource server migration causes the two modes to converge — see Resource Server Migration: Impact on mural-oauth for the full analysis.

The full picture under the resource server model (applicable to both modes post-migration):

1. **One-time setup (browser):** User authenticates with the external IdP (Layer 1 identity), then completes Mural OAuth (Layer 2 API access). Banksy stores Mural tokens.
2. **Every MCP request (IDE):** IDE presents an externally-issued JWT. Banksy validates it, looks up the user's Mural tokens in Postgres, executes the tool call against the Mural API, and returns the result.

### Open Questions

The resource server recommendation raises three questions that the audit does not answer. The first and third are now resolved; the second remains open pending a proof-of-concept.

**Who is the external Identity Provider? (Resolved — see External IdP Selection)** The audit says "IdP-issued JWTs" but doesn't specify which IdP. Google ID tokens are JWTs validatable via JWKS but don't support custom audiences or scopes. Mural's tokens are functionally opaque to external validators (this resolves Risk 1 from the FastMCP auth strategy plan). A dedicated IdP (Auth0, Azure AD) gives full control but introduces new infrastructure. The full candidate assessment is in External IdP Selection.

**Does the external IdP cover all Mural user segments? (Open — see Non-Enterprise User Gap)** The IdP candidates assume users have accounts with the external provider. This fails for Mural self-serve users who authenticate via email/password. Google excludes non-Google users; a dedicated IdP without custom connections requires a new account. The recommended resolution — a dedicated IdP with Mural as a custom upstream OAuth provider — is evaluated in the Non-Enterprise User Gap subsection of External IdP Selection. This depends on a PoC validating the custom social connection flow and upstream token storage. A comprehensive provider evaluation is scoped separately (see `banksy/.cursor/prompts/research-auth-provider-alternatives.md`).

**Do IDE MCP clients support PRM? (Resolved — see IDE Support for PRM)** The 2025-11-25 MCP spec makes PRM mandatory. Cursor, VS Code with GitHub Copilot, and Claude Desktop all implement PRM discovery today. Two smaller clients (Zed, Continue.dev) do not support remote MCP OAuth at all, but they cannot authenticate with Banksy's current AS model either — no regression. The remaining question is which FastMCP authentication class and external IdP to use.

---

## Ticket-by-Ticket Assessment

### Tickets Safe to Implement Now

Seven of the eight tickets have "fix in current flow" recommendations that harden the existing architecture without changing auth flows. These are safe to ship independently of the FastMCP migration.

**Ticket 1 (Critical) — OAuth Login CSRF:** The Google OAuth callback does not strictly enforce state validation. When state decoding fails, the flow logs a warning but continues to create a session. The fix is straightforward: generate cryptographically secure state server-side, store it with a TTL bound to the browser session, reject on mismatch or expiration, and make state single-use. This preserves the Google OAuth callback flow exactly as it works today — it just adds the guardrails that the OAuth spec requires. *Mural-oauth applicability:* Substantially addressed but not fully closed. The mural-oauth mode uses HMAC-SHA256 signed state with `crypto.timingSafeEqual` constant-time comparison and a 10-minute TTL (`STATE_MAX_AGE_MS`) — a significant improvement over sso-proxy's "log warning and continue" behavior. However, the state is not single-use: there is no server-side nonce storage or replay tracking, so a valid state token can be reused within the TTL window if an attacker intercepts the callback URL. This is mitigated by Mural's single-use authorization codes, but it is a defense-in-depth gap. The fix for mural-oauth is to add server-side nonce tracking with TTL and reject replayed states.

**Ticket 2 (Critical) — Open Redirect:** The callback URL validation uses `url.startsWith('/')` which accepts protocol-relative URLs like `//evil.com`. The fix is to parse with `new URL(url, baseOrigin)` and require `u.origin === baseOrigin`. Same flow, stricter validation. *Mural-oauth applicability:* Directly applies — the `isValidCallbackUrl` function in `mural-oauth.ts` has the same vulnerability. Its first check, `url.startsWith('/')`, returns `true` for protocol-relative URLs like `//evil.com`, bypassing the origin comparison that follows. The function does have a stronger second branch that parses with `new URL()` and compares origins, but the early return on `startsWith('/')` means that branch is never reached for protocol-relative inputs. The same fix applies: parse all URLs with `new URL(url, baseOrigin)` and require origin match unconditionally.

**Ticket 4 (High) — Security Headers:** Auth pages lack CSP, HSTS, Referrer-Policy, and anti-framing headers. This is purely additive — configure at Azure Front Door or app middleware. No flow changes. *Mural-oauth applicability:* Applies equally. The mural-oauth callback SPA and sign-in pages serve from the same infrastructure and lack the same headers.

**Ticket 5 (High) — Mural Claim Race Condition:** The `/auth/mural/claim` endpoint reads pending state, claims tokens, saves them, and deletes the pending record in separate non-atomic steps. Two concurrent claims can both succeed. The fix is to add a `claimedAt` or `status` column and implement an atomic compare-and-set transition. The claim flow stays the same — it just becomes safe under concurrency. The audit correctly notes this becomes more important under FastMCP, which will increase concurrency on tool invocations. *Mural-oauth applicability:* The specific claim endpoint race does not exist in mural-oauth mode, which has no `/auth/mural/claim` endpoint. However, a related concern exists: the token exchange and storage in the OAuth callback could face concurrent requests if a user triggers multiple authorization flows simultaneously. The `saveTokens` function uses `INSERT ... ON CONFLICT DO UPDATE`, which is atomic at the database level — significantly safer than sso-proxy's multi-step read-claim-save-delete pattern. This is lower risk but should be verified under concurrent load.

**Ticket 6 (High) — Plaintext Refresh Tokens:** Mural refresh tokens are stored unencrypted in Postgres. The fix is envelope encryption with Azure Key Vault. This is transparent to callers — the token storage layer encrypts on write and decrypts on read. *Mural-oauth applicability:* Directly applies. The `muralOauthToken` table stores `accessToken` and `refreshToken` as plain text strings, identically to `muralSessionToken`. The same envelope encryption fix is needed, and the shared token storage abstraction in `mural-tokens.ts` means the fix can be applied once to cover both tables.

**Ticket 7 (Medium) — OAuth Codes in URL History:** After reading the authorization code from the callback URL, the SPA doesn't call `history.replaceState()` to scrub it from browser history. A one-line fix. *Mural-oauth applicability:* Directly applies. The `oauth-callback.tsx` component reads the authorization code and state from the URL query parameters but does not call `history.replaceState()` to scrub them. The same one-line fix is needed.

**Ticket 8 (Medium) — Sensitive Auth Logging:** Auth flows log truncated state and code values. Even truncated secrets are sensitive in aggregate. Replace with correlation IDs and high-level event names. *Mural-oauth applicability:* Partially applies but less severe. The mural-oauth mode logs `callbackURL` (user-controlled input) and rejected URLs, and passes Mural's `error_description` through to the client — but it does not log tokens or authorization codes, which is an improvement over sso-proxy. The client ID is truncated to 8 characters in logs. The fixes needed are: stop logging full `callbackURL` values (use a boolean or hash), return a generic error message to the browser instead of forwarding `error_description`, and log `error_description` server-side only.

### The Migration Ticket

**Ticket 3 (High) — Remove Legacy MCP OAuth Surface:** This is the only ticket that would break existing behavior. It recommends removing the Better Auth MCP plugin and the `/api/auth/mcp/*` endpoints, along with the `/.well-known/oauth-authorization-server` metadata endpoint. This is not a bug fix — it's the architectural migration itself, scoped as a ticket. IDE compatibility with the resource server model has been confirmed for the three major clients (Cursor, VS Code, Claude Desktop), so the remaining prerequisites are selecting an external IdP and implementing the replacement auth mechanism using FastMCP's `RemoteAuthProvider` or `OAuthProxy`. Ticket 3 should be executed only after that replacement is in place and tested against the target IDEs. *Mural-oauth applicability:* Applies equally. The mural-oauth mode uses the same Better Auth MCP plugin to serve `/.well-known/oauth-authorization-server` and handle MCP token issuance. The resource server migration removes this surface from both modes simultaneously.

---

## Mural-OAuth Mode: Security and Migration Analysis

### How mural-oauth Differs from sso-proxy

The `mural-oauth` auth mode was introduced after the security audit. It allows Banksy to operate as a native OAuth integration with Mural. When a user adds Banksy to their IDE, the MCP OAuth flow redirects to Mural's own OAuth consent page. After the user authorizes, Mural redirects back to Banksy's callback, where an SPA exchanges the authorization code for Mural OAuth tokens (access + refresh). The callback plugin fetches user info from Mural's public API, creates or finds a Better Auth user, creates a session, stores the Mural tokens in Postgres, and completes the MCP flow.

The key architectural difference from sso-proxy is that identity and API access come from the same source — Mural — via a single OAuth flow, rather than the two-step flow in sso-proxy mode (Google sign-in plus separate Mural session activation). This mode uses the Mural Public API (`/api/public/v1/`) rather than internal content endpoints for widget operations. Tokens are stored in the `muralOauthToken` table (as opposed to `muralSessionToken` for sso-proxy), and the token type is `oauth` rather than `session`.

Despite appearing to collapse the two auth layers into one, the underlying architecture still has both layers. Better Auth's MCP plugin still serves as the Authorization Server for IDE connections (`/.well-known/oauth-authorization-server`), and the Mural OAuth flow is embedded within the MCP authorization flow rather than being independent. The distinction matters for the resource server migration, as discussed below.

### Security Posture: Ticket Extrapolation Summary

Of the eight audit tickets, six apply to mural-oauth mode with varying severity. Two of the critical tickets — CSRF and open redirect — are partially or fully present, though the CSRF implementation is significantly stronger than in sso-proxy mode.

**Tickets that directly apply (same fix needed):** Ticket 2 (open redirect — identical `startsWith('/')` vulnerability), Ticket 4 (security headers), Ticket 6 (plaintext refresh tokens in `muralOauthToken`), and Ticket 7 (authorization code not scrubbed from browser history in `oauth-callback.tsx`). These are the same bugs in the same patterns, and the same fixes work. The token encryption fix (Ticket 6) is particularly important because the `muralOauthToken` table persists post-migration — Banksy will continue to store Mural API tokens regardless of how IDE authentication evolves.

**Tickets partially addressed by design:** Ticket 1 (CSRF) is substantially mitigated. The mural-oauth state parameter uses HMAC-SHA256 with the Better Auth secret, includes a cryptographic nonce and timestamp, enforces a 10-minute TTL, and uses constant-time comparison via `crypto.timingSafeEqual`. This is materially stronger than sso-proxy's approach, where state validation failure logs a warning but allows the flow to continue. The remaining gap is that state is not single-use — the same signed state can be replayed within the TTL window. This is mitigated by Mural's single-use authorization codes (a replayed state with a consumed code will fail at Mural), but defense-in-depth calls for server-side nonce tracking.

**Tickets with reduced applicability:** Ticket 5 (race condition) does not apply in its original form because mural-oauth has no `/auth/mural/claim` endpoint. The analogous concern — concurrent token exchanges for the same user — is handled by `INSERT ... ON CONFLICT DO UPDATE` in the token storage layer, which is atomic. Ticket 8 (sensitive logging) is less severe in mural-oauth, which does not log tokens or authorization codes. It does log `callbackURL` values and passes `error_description` from Mural to the browser, both of which should be cleaned up.

**Ticket that applies identically:** Ticket 3 (remove MCP OAuth surface) applies equally, since both modes use the same Better Auth MCP plugin infrastructure.

### New Security Concerns

Beyond the eight audit tickets, the mural-oauth implementation introduces several concerns that did not exist in sso-proxy mode.

**State replay window.** The HMAC-signed state has a 10-minute window during which it can be replayed. There is no server-side nonce storage, no database record of used states, and no mechanism to mark a state as consumed after successful use. While Mural's single-use authorization codes limit the practical impact — an attacker would need both a valid state and an unconsumed code — the 10-minute window is wider than necessary. The recommendation is to store used nonces in Redis or the database with a TTL matching `STATE_MAX_AGE_MS` and reject any state whose nonce has been seen before.

**Better Auth secret as CSRF root of trust.** The state HMAC is keyed with the Better Auth secret (`ctx.context.secret`). If this secret is compromised, an attacker can forge valid state parameters for arbitrary callback URLs, enabling login CSRF without interception. This is not a vulnerability per se — any HMAC-based state scheme depends on secret integrity — but it means the Better Auth secret is a higher-value target in mural-oauth mode than in sso-proxy mode, where state validation is weaker and therefore less of a security boundary.

**SPA error parameter reflection.** The `oauth-callback.tsx` component reads `error` and `error_description` from URL query parameters and renders them in the UI. React's default text escaping prevents script injection when these values are rendered as text children, but the values are not sanitized or validated before display. If a UI Toolkit component were to render the value as HTML (via `dangerouslySetInnerHTML` or similar), or if Mural's `error_description` contained misleading content, this could become a phishing or XSS vector. The recommendation is to return a generic error message to the user and log the specific `error_description` server-side only.

**Scope breadth.** The default Mural OAuth scopes configured in `config.ts` are broad: `murals:read`, `murals:write`, `workspaces:read`, `rooms:read`, `identity:read`, `templates:read`. These scopes are overridable via the `MURAL_OAUTH_SCOPES` environment variable, but there is no runtime validation that the granted scopes match what the tool set requires. The principle of least privilege suggests auditing the tool set to determine the minimum required scopes and removing any that are not used.

**Client credentials in environment.** The mural-oauth mode requires `MURAL_OAUTH_CLIENT_ID` and `MURAL_OAUTH_CLIENT_SECRET` as environment variables. The client secret is handled appropriately — it is passed only to the token exchange function and the HMAC generation, and the client ID is truncated to 8 characters in log output. This is acceptable, but the credentials should be rotated periodically and stored in a secrets manager (Azure Key Vault) rather than plain environment variables in production.

### Resource Server Migration: Impact on mural-oauth

The resource server migration causes the mural-oauth and sso-proxy modes to converge architecturally. This is the most significant finding of this analysis.

Today, mural-oauth's value proposition is a single-step user experience: one Mural OAuth consent provides both identity and API access. Under the resource server model, Layer 1 must use an external IdP whose tokens Banksy can validate via JWKS. Mural does not qualify — the three blockers identified in the Mural-as-IdP assessment (see External IdP Selection) apply regardless of auth mode, and no FastMCP auth class can work around them.

The consequence is that post-migration, mural-oauth loses its single-step advantage — unless the external IdP supports upstream token storage. If the dedicated IdP is configured with Mural as a custom social connection and supports storing Mural's access and refresh tokens during the social login flow (as Auth0's Token Vault does for Enterprise customers), Banksy could retrieve those tokens server-to-server after the user authenticates. In this scenario, the user completes one authentication with Mural (through the IdP), and Banksy obtains both an IdP-issued JWT (Layer 1) and Mural API tokens (Layer 2) without a separate browser step. This would preserve mural-oauth's single-step UX under the resource server model. See the "Non-Enterprise User Gap" subsection in External IdP Selection for the full analysis of this approach, including spec compliance and limitations.

Without upstream token storage, the user would authenticate with the external IdP via the IDE (Layer 1), then separately complete Mural OAuth in the browser to authorize API access (Layer 2). This is structurally identical to the sso-proxy model. The Mural OAuth token exchange, storage, and refresh logic in `mural-oauth.ts` would persist as the Layer 2 "Mural connect" mechanism, while the Better Auth MCP plugin and `/.well-known/oauth-authorization-server` endpoint would be replaced by PRM and the external IdP.

The practical implications depend on whether the external IdP supports upstream token storage (see Token capture for Layer 2 in the Non-Enterprise User Gap subsection of External IdP Selection):

| Aspect | Current mural-oauth | Post-migration (without token storage) | Post-migration (with token storage) |
|---|---|---|---|
| Layer 1 (IDE auth) | Better Auth MCP plugin (AS) | External IdP via PRM (RS) | External IdP via PRM (RS) |
| Layer 2 (Mural API) | Mural OAuth (same flow as Layer 1) | Mural OAuth (separate browser step) | IdP retrieves Mural tokens during social login |
| User steps | 1 (Mural consent) | 2 (IdP auth + Mural connect) | 1 (Mural consent via IdP) |
| Token storage | Needed (`muralOauthToken`) | Still needed (Banksy stores) | IdP stores upstream tokens; Banksy retrieves |
| SPA callback | Needed for OAuth callback | Still needed for Layer 2 | Not needed (no separate browser step) |
| `/.well-known` | `oauth-authorization-server` | `oauth-protected-resource` | `oauth-protected-resource` |

This convergence simplifies the migration in one respect: there is a single target architecture regardless of which auth mode is the starting point. The same external IdP, the same FastMCP auth class, and the same PRM configuration serve both modes. The mural-oauth token exchange and storage logic becomes the universal Layer 2 mechanism — its use of the Mural Public API (rather than internal content endpoints) and standard OAuth tokens (rather than session-activation JWTs) makes it the more portable Layer 2 implementation.

### Practical Assessment

**Tickets needing implementation work for mural-oauth:** Tickets 1, 2, 6, 7, and 8 all need fixes. Ticket 2 (open redirect) and Ticket 6 (plaintext tokens) are the most urgent. Ticket 1 needs a single-use nonce check. Ticket 7 is a one-line `history.replaceState()` addition. Ticket 8 needs logging cleanup.

**New hardening beyond the audit:** Add server-side nonce tracking for OAuth state, return generic error messages instead of forwarding `error_description`, and audit Mural OAuth scopes for minimum privilege.

**Migration path:** The resource server migration is the same for both modes — the target architecture is identical. The migration subsumes Ticket 3 for both modes. The mural-oauth token exchange and storage logic survives the migration as the Layer 2 mechanism.

**Recommended sequencing:** Fix audit tickets in mural-oauth first (Tickets 1, 2, 6, 7, 8), then migrate. The migration does not subsume these fixes because: (a) the open redirect and CSRF vulnerabilities exist in the current production system, (b) the token encryption fix applies to the storage layer that persists post-migration, and (c) the migration timeline is uncertain. Ticket 5 (race condition) does not need a separate fix for mural-oauth — the atomic upsert is already sufficient.

---

## Outstanding Research

### IDE Support for Protected Resource Metadata (Resolved)

The resource server model is the only authentication model defined by the current MCP specification. PR #338 (merged April 23, 2025) formally separated MCP servers from authorization servers, and the 2025-11-25 spec revision codified this as normative: MCP servers MUST implement OAuth 2.0 Protected Resource Metadata (RFC 9728), and MCP clients MUST use RFC 9728 for authorization server discovery. The spec explicitly states that "a protected MCP server acts as an OAuth 2.1 resource server." There is no longer a spec-defined path where MCP servers act as authorization servers. Banksy's current model — Better Auth's MCP plugin serving `/.well-known/oauth-authorization-server` — works only because IDE clients still attempt legacy discovery as a fallback, a behavior that will erode as clients converge on the 2025-11-25 spec.

The three dominant IDE MCP clients all support PRM discovery today. Cursor has shipped PRM support since v1.0 (June 2025) and correctly discovers `/.well-known/oauth-protected-resource`, follows `authorization_servers` links to external IdPs, and completes OAuth 2.1 authorization code flows with PKCE against those IdPs. There is a known bug (reported February 2026, acknowledged by the Cursor team) where the `resource_metadata` URL extracted from a `WWW-Authenticate` header is lost after the OAuth redirect, causing token exchange to fall back to well-known path discovery. This bug only affects servers that rely on `WWW-Authenticate` to advertise a non-standard metadata path — servers that serve metadata at the standard `/.well-known/oauth-protected-resource` path are unaffected. VS Code with GitHub Copilot reached general availability for MCP in July 2025 (v1.102) with full PRM support, including OIDC Discovery and RFC 8414 Authorization Server Metadata discovery patterns. Microsoft actively documents integration with Azure Entra ID using the resource server model. Claude Desktop supports OAuth 2.1 with PRM discovery over streamable HTTP transport.

Two smaller IDE clients do not yet support remote MCP OAuth at all. Zed has an open bug (#43162, February 2026) where the OAuth authentication flow fails to trigger for remote MCP servers — PRM discovery never starts. Continue.dev has an open enhancement request (#6282, June 2025) to implement MCP OAuth authorization; until that ships, it only works with unauthenticated or pre-configured MCP servers. Neither of these clients supports the current "Banksy as AS" model either, so the resource server migration does not regress their capabilities.

Windsurf (Codeium) has MCP integration but documentation focuses on local stdio-based servers with environment variable credentials. There is no clear evidence of remote OAuth PRM support in Windsurf today.

On the SDK side, the TypeScript MCP SDK (v1.27.1) implements `discoverOAuthProtectedResourceMetadata()` with a known bug (Issue #1234, fix in PR #1350) where the resource metadata URL is lost after browser redirects. The Python MCP SDK has an open PR (#982) implementing the AS/RS separation.

A critical finding concerns FastMCP's authentication classes. The audit references `JWTVerifier` / `TokenVerifier` as the resource server mechanism, but bare `JWTVerifier` does not serve PRM — IDE clients would have no discovery metadata. FastMCP provides two IDE-facing auth classes: `RemoteAuthProvider` (for DCR-capable IdPs) and `OAuthProxy` (for non-DCR IdPs). Both serve PRM and produce spec-compliant resource servers. The choice depends on the external IdP's capabilities — see External IdP Selection for the full comparison.

Working examples of MCP servers in resource server mode exist today. Microsoft documents a complete Entra ID integration with VS Code. The mcp-auth.dev project provides reference implementations. Quarkus has published a tutorial for OAuth-protected MCP servers. These examples confirm that the pattern works end-to-end with real IDE clients when the server serves PRM at the standard well-known path.

The resource server model is viable today for the three major IDEs with two operational requirements: Banksy must serve PRM at the standard `/.well-known/oauth-protected-resource` path (not solely via `WWW-Authenticate`), and it must use FastMCP's `RemoteAuthProvider` or `OAuthProxy` rather than bare `JWTVerifier`. A hybrid transition serving both well-known endpoints is technically possible but unnecessary — the three major IDEs already prefer PRM discovery.

### External IdP Selection

Choosing the external IdP is the primary remaining architectural decision. The choice determines JWT validation configuration and which FastMCP authentication class to use, because the two classes have different IdP requirements.

FastMCP's `RemoteAuthProvider` requires an IdP that supports Dynamic Client Registration (RFC 7591). With DCR, IDE clients can automatically register themselves with the IdP and obtain credentials without manual configuration — this is the cleanest experience. IdPs with DCR support include WorkOS AuthKit, Descope, and some modern OIDC providers. A dedicated IdP like Auth0 can also support DCR if configured. `RemoteAuthProvider` composes a `JWTVerifier` for token validation with automatic PRM endpoint generation, producing a pure resource server with no AS-like surface area.

FastMCP's `OAuthProxy` is designed for IdPs that do not support DCR — which includes Google, Azure AD, and GitHub. It bridges the gap by presenting a DCR-compliant interface to MCP clients while holding pre-registered client credentials for the upstream provider. The IDE client dynamically registers with the OAuthProxy (which acts as a thin intermediary), and the proxy forwards the authorization flow to the upstream IdP using its fixed credentials. This reintroduces some AS-like surface area on Banksy — it still manages DCR registrations and proxies token requests — but significantly less than the current Better Auth model, and the token validation itself is still delegated to the external IdP's JWT infrastructure.

Regardless of which IdP is chosen for Layer 1, Layer 2 remains unchanged — the resource server migration replaces how the IDE proves identity to Banksy, not how Banksy obtains or manages Mural API tokens (see What This Changes). The only candidate that could theoretically collapse both layers is Mural-as-IdP, assessed below.

The candidate IdPs, reassessed with this information:

**Google** is already used for Banksy's Layer 1 identity (SSO proxy). Google access tokens are opaque, but Google ID tokens are JWTs validatable via Google's JWKS endpoint. The limitation is that Google does not support custom audiences or scopes for third-party applications — the audience of a Google ID token is the OAuth client ID, and scopes are limited to Google's predefined set (openid, email, profile). Google also does not support DCR, so it would require `OAuthProxy`. This may be sufficient if Banksy only needs to verify "this is a legitimate Google user" and looks up Mural API tokens by user ID, but it provides no mechanism for fine-grained MCP scope control.

**Mural OAuth** was investigated as a candidate that could collapse both auth layers — the IDE authenticates directly with Mural, and Banksy uses the same token for API calls, eliminating server-side token storage and the browser-based connection step. This investigation resolved Risk 1 from the FastMCP auth strategy plan (Mural's token format) and identified three independent blockers, each individually fatal.

*Blocker 1: HS256 tokens with no JWKS.* Mural OAuth access tokens are JWTs signed with HS256 using a symmetric shared secret (`jwt.sign(claims, config.jwt.secret)` in `api/src/core/session/tokens/index.ts`). Validation uses the same secret (`algorithms: ['HS256']` in `api/src/security/jwt/index.ts`). There is no JWKS endpoint, no asymmetric key pair, and no issuer or audience claims. Banksy cannot validate these tokens without possessing Mural's `config.jwt.secret` — a severe security boundary violation that would give Banksy the ability to forge arbitrary Mural tokens. HS256 tokens without a JWKS endpoint are functionally equivalent to opaque tokens for external validators.

*Blocker 2: No OAuth discovery infrastructure.* Mural does not serve `/.well-known/oauth-authorization-server`, `/.well-known/openid-configuration`, or any RFC 8414 / OIDC Discovery metadata. Mural also does not support Dynamic Client Registration (RFC 7591) — OAuth clients are pre-registered and loaded by ID from internal storage. IDE clients performing PRM discovery would follow the `authorization_servers` link to Mural and find no metadata endpoints to complete the OAuth flow. Neither `RemoteAuthProvider` nor `OAuthProxy` can work with an IdP that has no discovery endpoint.

*Blocker 3: MCP spec token passthrough prohibition.* Even if Mural added JWKS and discovery endpoints, the architecture would violate the MCP specification's security model. The spec states: "The MCP server MUST NOT pass through the token it received from the MCP client." The rationale is preventing confused-deputy attacks where a token intended for one audience is used at another. The Mural-as-IdP model does exactly this — Banksy receives a Mural token from the IDE and uses it to call the Mural API.

RFC 8707 audience binding makes this contradiction structural. MCP clients MUST include the `resource` parameter binding the token's audience to the MCP server's URI. A token with `resource=https://banksy.example.com` is audience-bound to Banksy — Mural's API would reject it. A token audience-bound to Mural's API would fail Banksy's validation ("MCP servers MUST only accept tokens intended for themselves"). No audience value satisfies both constraints.

The prohibition is absolute regardless of intent: the MCP server must not use the client's token for upstream calls. The two-layer architecture — IdP-issued JWT for Layer 1, separately stored Mural tokens for Layer 2 — is the spec-compliant pattern.

Mural-as-IdP is not viable. Blockers 1 and 2 would require Mural to change its OAuth infrastructure; Blocker 3 is a fundamental MCP protocol constraint that would persist regardless.

The existence of `mural-oauth` mode does not change this conclusion. One might expect `OAuthProxy` configured with Mural as the upstream provider to formalize mural-oauth's current arrangement under the resource server model. But `OAuthProxy` delegates signature verification to the upstream IdP's JWKS endpoint — Mural has none, so all three blockers apply equally. The mural-oauth mode's single-step UX is a product of Banksy acting as an AS intermediary between the IDE and Mural — a role the resource server migration eliminates by design.

**A dedicated IdP** (Auth0, Azure AD / Entra ID, WorkOS, Descope) gives full control over JWT shape, JWKS endpoints, custom audiences, custom scopes, and token lifetimes. Auth0, WorkOS, and Descope support DCR, enabling `RemoteAuthProvider` for a pure resource server. Azure AD does not support DCR for arbitrary clients, so it would require `OAuthProxy`. A dedicated IdP introduces new infrastructure and potentially another user account, but it eliminates dependency on Google's token format limitations and provides the flexibility to define Banksy-specific scopes like `mcp:tools`, `mural:read`, or `mural:write`.

The choice cascades into JWT validation configuration (JWKS URI, issuer, audience), scope/audience design, user account linking (mapping IdP subject to Banksy user records), and the operational burden of a new IdP dependency. A dedicated IdP with DCR support and `RemoteAuthProvider` is the architecturally cleanest option but has the highest setup cost. Google with `OAuthProxy` is the lowest-friction option but sacrifices scope control. However, IdP choice has a more fundamental implication than scope control: it determines which Mural user segments can use Banksy at all.

### The Non-Enterprise User Gap

The preceding IdP assessment implicitly assumes that users have accounts with the external Identity Provider. This assumption holds for enterprise users with SSO and for users who happen to have Google accounts, but it fails for a significant segment of Mural's user base: self-serve users on individual or team plans who authenticate with Mural directly via email and password.

Mural supports five authentication methods, confirmed from the mural-api codebase: email/password sign-in (`api/src/api/session/signin.ts`), Google social login (`data/src/data/models/idp/providers/google.ts`), Microsoft social login (`data/src/data/models/idp/providers/microsoft.ts`), SAML SSO (`api/src/api/authenticate/saml2/`), and OAuth2 SSO (`api/src/api/authenticate/oauth2/authorization/`). The mural-oauth mode was specifically designed to reach all of these user segments — Mural's OAuth consent page handles authentication regardless of method, and Banksy obtains identity and API tokens from the resulting flow. The resource server migration, as currently described, would regress this capability.

Under each IdP candidate evaluated above, the user coverage gap is:

**Google as IdP** excludes every Mural user who does not have a Google account. This includes email/password users, Microsoft social login users, and users whose enterprise SSO is through a non-Google provider (Okta, Azure AD via SAML, etc.). While the exact proportions are not publicly available, the combination of email/password users (Mural's original sign-up method) and non-Google social/SSO users likely represents a majority of Mural's user base. Using Google as the IdP would make Banksy accessible only to the subset of users who happen to have Google accounts — a severe regression from the current mural-oauth mode, which serves all users.

**A dedicated IdP without custom connections** (e.g., a standard Auth0 or WorkOS deployment) presents a different problem: no Mural user has a pre-existing account with the IdP. Users would need to create a new account (e.g., sign up for Auth0) to use Banksy. This is a disjointed experience — users already have a Mural account and would not understand why a separate identity account is needed to use a Mural integration. It also decouples Banksy's identity from Mural's identity, creating a user-mapping problem: Banksy would need to link the IdP's user subject to the Mural user ID, but without Mural as a data source during authentication, the link must be established through a separate flow.

**Mural-as-IdP** covers all users by definition, since all Mural users authenticate with Mural. However, this option is blocked by the three technical issues identified above (HS256 tokens with no JWKS, no OAuth discovery, token passthrough prohibition) and is not viable.

This is an access issue, not a convenience issue. Under the resource server model with Google or a standard dedicated IdP, an entire class of users loses the ability to use Banksy — a regression from the current system where any Mural user can authenticate regardless of how they created their account.

#### Resolution Option 1: Dedicated IdP with Mural as a Custom Social Connection

The most promising resolution is a dedicated IdP configured with Mural as a custom upstream OAuth provider. This is a "buy" approach — the dedicated IdP provides the protocol infrastructure (JWKS, discovery, DCR, RS256 JWTs) that the resource server model requires, while delegating actual authentication to Mural.

Services like Auth0 support custom social connections where you manually configure an upstream OAuth provider's authorization URL, token URL, and user info URL — no discovery metadata on the upstream side is required. Descope supports a similar custom OAuth provider pattern. The flow is:

1. IDE discovers Banksy's PRM → follows `authorization_servers` link to the dedicated IdP
2. The IdP can be configured to skip its own login page and redirect directly to Mural when Mural is the sole configured connection (Auth0 calls this "Home Realm Discovery"). From the user's perspective, they see only the Mural login/consent page — the dedicated IdP is invisible infrastructure.
3. User authenticates with Mural using whatever method they have — email/password, Google, Microsoft, SAML SSO — and authorizes the Banksy integration
4. Mural redirects back to the IdP with an authorization code
5. The IdP exchanges the code for Mural tokens, fetches user info from Mural's public API, and issues its own RS256 JWT
6. IDE receives the IdP's JWT → presents to Banksy → Banksy validates via the IdP's JWKS

This addresses Blockers 1 and 2 from the Mural-as-IdP assessment — the dedicated IdP issues proper JWTs with JWKS and has full discovery. It sidesteps Blocker 3 because the token Banksy validates is IdP-issued, not Mural-issued — there is no passthrough. The user coverage problem is solved because Mural handles all authentication methods behind its consent page, and the dedicated IdP simply delegates to it.

**Token capture for Layer 2.** During step 5, the dedicated IdP obtains Mural access and refresh tokens as part of the social connection token exchange. Some providers offer secure storage and retrieval of these upstream tokens. Auth0's Token Vault stores upstream provider access and refresh tokens when users authenticate through social connections, and Banksy can retrieve them via Auth0's federated connection access token exchange — a server-to-server API call where Banksy presents its own Auth0 access token and receives the user's Mural tokens. If this works for custom social connections (which needs PoC validation), it would eliminate the separate "Mural connect" browser step entirely. The user authenticates once with Mural (through the dedicated IdP), and Banksy obtains both identity (IdP JWT) and Mural API tokens (from the IdP's token store) in a single flow. This would preserve the mural-oauth mode's single-step UX advantage — the key differentiator that the preceding analysis concluded would be lost post-migration.

**Spec compliance of token capture.** The MCP spec prohibits token passthrough: "MCP servers MUST NOT accept any tokens that were not explicitly issued for the MCP server." In the Token Vault pattern, the Mural token is never received from the MCP client. The IDE sends an IdP-issued JWT (audience-bound to Banksy via RFC 8707). Banksy retrieves the Mural token from the IdP's token store via a separate server-to-server channel. Architecturally, this is identical to the current pattern where Banksy stores Mural tokens in Postgres after a browser-based OAuth flow — the token comes from a server-side channel, not from the MCP client. The spec's prohibition targets the scenario where an MCP server receives a token from the client and forwards it to a downstream API; that does not describe this pattern.

**Limitations and open questions.** Auth0's Token Vault is an Enterprise add-on — it is not available on the Free, Essentials, or Professional tiers. Enterprise pricing is not published and requires a sales conversation. Custom Token Exchange is in Early Access as of 2026. Whether Token Vault supports refresh token storage and automatic refresh for custom social connections (as opposed to built-in providers like Google or GitHub) needs PoC validation. WorkOS AuthKit does not support custom upstream OAuth providers — its social login support is limited to predefined providers (Google, Microsoft, GitHub, Apple, and others), so it cannot be used for this pattern despite supporting DCR. Descope supports custom OAuth providers and DCR, making it a viable alternative to Auth0 — its pricing model and upstream token storage capabilities should be evaluated alongside Auth0. A comprehensive evaluation of candidate auth providers, including pricing analysis, is documented separately (see `banksy/.cursor/prompts/research-auth-provider-alternatives.md`).

Without upstream token storage (either because the IdP doesn't support it, or because its pricing tier is prohibitive), the dedicated IdP still solves the user coverage problem. All Mural user segments can authenticate, and Banksy validates the IdP's JWT for Layer 1. Layer 2 reverts to a separate browser-based Mural OAuth flow — the same two-step pattern described in the preceding migration analysis. The user experience is: authenticate with Mural through the IdP (Layer 1, in the IDE), then complete a separate Mural OAuth consent in the browser (Layer 2). This is a minor UX regression from the current mural-oauth mode's single step, but it is not an access regression — all users can still use Banksy.

#### Resolution Option 2: Mural Evolves Its OAuth Infrastructure

The "build" alternative to a dedicated IdP is for Mural's platform team to add the protocol infrastructure that the resource server model requires. This would eliminate the need for an intermediary and allow Mural to serve as the IdP directly. The three blockers identified in the Mural-as-IdP assessment would need to be addressed:

*Blocker 1 resolution: asymmetric signing and JWKS.* Mural would need to migrate from HS256 symmetric signing to RS256 or ES256 asymmetric signing and expose a JWKS endpoint. This is not a configuration change — it is deeply embedded in the codebase. Token signing in `api/src/core/session/tokens/index.ts` uses `jwt.sign(claims, config.jwt.secret)` where `config.jwt.secret` is a symmetric string, and `jsonwebtoken` defaults to HS256 when given a string secret. Token validation in `api/src/security/jwt/index.ts` is hardcoded to `algorithms: ['HS256']` with the same shared secret. Multiple token types (session, refresh, OAuth refresh, realtime, upload proxy) use separate HS256 secrets defined in `api/config/defaults.json`. Migrating to asymmetric signing would require: generating and managing RSA/EC key pairs, updating all token creation paths to use private keys, updating all validation paths to use public keys, exposing a JWKS endpoint serving the public keys, and handling key rotation. Existing tokens in circulation would need a transition period where both HS256 and RS256 are accepted.

*Blocker 2 resolution: OAuth discovery.* Mural would need to serve `/.well-known/oauth-authorization-server` or `/.well-known/openid-configuration` with standard metadata (issuer, authorization endpoint, token endpoint, JWKS URI, supported scopes, supported response types). Mural does not serve any discovery endpoints today. The authorization, token, and consent endpoints exist (`api/src/api/authenticate/oauth2/authorization/`), so the metadata document would describe existing infrastructure, but it would need to conform to RFC 8414 or OIDC Discovery standards.

*Blocker 3: token passthrough.* This blocker persists regardless. Even if Mural added JWKS and discovery, Banksy could not use the IDE-presented Mural token for Mural API calls due to the MCP spec's passthrough prohibition and audience binding constraints. Layer 2 would still be needed. However, if Mural additionally supported RFC 8693 (OAuth 2.0 Token Exchange), Banksy could present its validated Layer 1 token to Mural's token exchange endpoint and receive separate Layer 2 API tokens — server-to-server, without additional user interaction. This would preserve single-step UX without violating passthrough rules, because the Layer 2 tokens are independently obtained by Banksy, not passed through from the MCP client. Mural does not implement RFC 8693 today.

*DCR support.* Mural does not support Dynamic Client Registration — OAuth clients are pre-registered and loaded by ID from internal storage. Without DCR, `RemoteAuthProvider` cannot be used, and Banksy would need `OAuthProxy` to bridge DCR for IDE clients. This reintroduces some AS-like surface area but is workable.

This option covers all Mural user segments, keeps control in-house, and eliminates third-party vendor costs. However, it depends entirely on the Mural platform team prioritizing this work. No evidence of OIDC, JWKS, RS256, or discovery endpoint development was found in the mural-api codebase. The scope of changes — asymmetric signing migration across multiple token types, JWKS endpoint, discovery metadata, and ideally RFC 8693 token exchange — represents significant platform engineering effort. The timeline is uncertain and outside the Banksy team's control.

#### Resolution Option 3: Dual Auth Architecture

The pragmatic fallback is to maintain two authentication architectures: the current Better Auth MCP plugin (Authorization Server model) for deployments where Mural is the only identity source, and the resource server model for enterprise clients with existing IdPs.

The MCP spec says servers MUST implement Protected Resource Metadata. A server that conditionally serves PRM for some deployments and `/.well-known/oauth-authorization-server` for others is technically compliant on a per-instance basis — each instance implements one model. But it creates two codepaths, two sets of security concerns, two token validation mechanisms, and two sets of deployment configurations. The security audit's Ticket 3 (remove the AS surface) could only be applied to enterprise deployments, leaving the AS attack surface active for non-enterprise deployments indefinitely.

This option is sustainable as a transitional state — it preserves Banksy's current capability for non-enterprise users while the dedicated IdP or Mural infrastructure path matures. It is not a viable long-term architecture because: (a) the AS model will become increasingly non-compliant as IDE clients drop legacy discovery fallbacks, (b) maintaining two auth architectures doubles the surface area for security issues, and (c) the audit specifically recommends eliminating the AS surface.

#### Resolution Synthesis

The dedicated IdP with Mural as a custom social connection is the recommended path. It is the only option that simultaneously covers all Mural user segments, is fully MCP-spec-compliant, does not depend on the Mural platform team, and can be implemented by the Banksy team today. With upstream token storage (Auth0 Token Vault or equivalent), it can potentially preserve single-step UX; without it, users complete two steps but no one is excluded.

The Mural infrastructure evolution is the better long-term outcome — it eliminates vendor dependency and keeps the auth stack in-house — but its timeline is uncertain and outside Banksy's control. It should be pursued as a parallel conversation with the Mural platform team, but Banksy's migration planning should not block on it.

The dual auth architecture is an acceptable transitional state if the dedicated IdP PoC reveals unexpected issues, but it should not be the target architecture.

The IdP decision should now be informed by a proof-of-concept that validates: (a) the custom social connection flow with Mural as the upstream provider, (b) whether upstream token storage works for custom connections (enabling single-step UX), and (c) the end-to-end flow with at least Cursor and VS Code. A comprehensive evaluation of candidate auth providers — including Auth0, Descope, and other alternatives — with pricing analysis is scoped as a separate research effort (see `banksy/.cursor/prompts/research-auth-provider-alternatives.md`).

---

## Relationship to the FastMCP Migration

The security audit and the FastMCP migration are related but separable concerns. The "fix in current flow" items from the audit should be treated as independent security hardening work that improves the current system regardless of whether or when the FastMCP migration happens. The "FastMCP design direction" items (primarily Ticket 3) are migration planning inputs.

The existing FastMCP auth strategy plan (see `fastmcp_auth_strategy_f355d421.plan.md`) identifies several risks that overlap with the audit's concerns. Risk 1 (whether Mural tokens are opaque) is now resolved: they are JWTs signed with HS256 and no JWKS, ruling out Mural-as-IdP (see External IdP Selection). Risks 2 and 4 — `get_access_token()` return value and token refresh lifecycle differences — remain open. The audit adds a new dimension: the choice between FastMCP's `OAuthProxy` and `RemoteAuthProvider`, which depends on the external IdP's DCR capability (see External IdP Selection). IDE compatibility with the resource server model has been confirmed for Cursor, VS Code, and Claude Desktop. The remaining decision is IdP selection.

---

## Summary of Recommended Sequencing

1. **Now (both modes):** Implement Tickets 1, 2, 4, 6, 7, 8 as backward-compatible hardening across both sso-proxy and mural-oauth modes. Ticket 5 (race condition) needs a fix for sso-proxy's claim endpoint; mural-oauth's atomic upsert is already sufficient. For mural-oauth specifically, add server-side nonce tracking for OAuth state (completing the Ticket 1 fix), return generic errors instead of forwarding `error_description` to the browser, and audit Mural OAuth scopes for minimum privilege.
2. **Auth provider evaluation and PoC:** The non-enterprise user gap analysis (see External IdP Selection) establishes that the external IdP must support custom upstream OAuth connections with Mural as the identity source — otherwise, email/password and non-Google Mural users are excluded. Evaluate candidate auth providers (Auth0, Descope, and others — see `banksy/.cursor/prompts/research-auth-provider-alternatives.md` for the full evaluation scope) for technical fit, pricing, and operational complexity. Run a PoC with the top candidate(s) to validate: (a) the custom social connection flow with Mural as the upstream provider, (b) whether upstream token storage works for custom connections (enabling single-step UX), and (c) the end-to-end flow with at least Cursor and VS Code. In parallel, open a conversation with the Mural platform team about JWKS, discovery, and RFC 8693 support — if Mural commits to this work on a reasonable timeline, it changes the build-vs-buy calculus.
3. **IdP decision:** Based on the PoC results, select the external auth provider. The choice determines the FastMCP authentication class: a DCR-capable provider (Auth0, Descope) with `RemoteAuthProvider`, or a non-DCR provider with `OAuthProxy`. This applies to both auth modes — they converge on the same architecture post-migration.
4. **Migration:** Implement the FastMCP resource server using the chosen provider and auth class. The mural-oauth token exchange and storage logic becomes the universal Layer 2 "Mural connect" mechanism for both modes post-migration, since its use of the Mural Public API and standard OAuth tokens makes it the more portable implementation. If the chosen provider supports upstream token storage, Layer 2 can be embedded in the Layer 1 flow, preserving single-step UX. Validate the end-to-end flow (PRM discovery, IdP authentication, token validation, Mural connect, tool invocation) with the three major IDEs before proceeding.
5. **Ticket 3:** Remove the Better Auth MCP plugin and `/api/auth/mcp/*` endpoints only after the replacement auth mechanism is deployed and proven. This removes the AS surface from both modes simultaneously.
