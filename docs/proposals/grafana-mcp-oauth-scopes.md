# Proposal: review unexpected Grafana Cloud MCP scopes

Status: proposed. This document does not change runtime authorization.

## Problem and evidence

Grafana Cloud MCP sign-in can finish its OAuth token exchange but still fail
before SecondShift saves a connection. Grafana advertises only
`grafana:read`, `grafana:query`, and `grafana:write` in both its protected-resource
and authorization-server metadata. In an observed read-only authorization,
the token endpoint returned `grafana:read` plus:

```text
mcp:admin
mcp:ai-observability
mcp:alerting
mcp:assistant
mcp:business-data
mcp:dashboards
mcp:observability
mcp:raw-api
```

SecondShift rejects any granted scope that was not requested. That protection
currently prevents this connection. Clearing Query and Write on Grafana's
consent screen did not remove the extra scopes in the observed response.

Public metadata can be checked at:

- `https://mcp.grafana.com/.well-known/oauth-protected-resource/mcp/<stack>.grafana.net`
- `https://mcp.grafana.com/.well-known/oauth-authorization-server/mcp/<stack>.grafana.net`

Grafana documents Read, Query, and Write as distinct access choices and says
the user's Grafana permissions continue to apply. It does not establish the
authorization meaning of the extra scope strings above. Their names alone do
not prove either administrative access or harmless tool categorization.

Sources:

- [Grafana Cloud MCP](https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/cloud-mcp/)
- [Register an OAuth client](https://grafana.com/docs/grafana-cloud/ai-tools/mcp-servers/register-oauth-client/)

## Proposed behavior

Keep rejecting unexpected scopes by default. Do not ignore an `mcp:*` prefix,
globally accept scope supersets, or infer that unknown scopes are harmless.

For the official Grafana Cloud MCP service, offer the workspace owner an
explicit review step after token exchange and before connection creation:

1. Compare the requested and returned scopes. Continue rejecting any
   unrequested `grafana:query` or `grafana:write`, and any unknown scope outside
   the exact observed extra-scope set. Such a response requires new analysis,
   not an automatically expanded exception.
2. If the only unexpected values are members of that exact set, show the
   requested access, all returned scopes, and the exact additional scopes.
   Explain that the extra scopes' meanings are unverified; call out names
   such as `mcp:admin` and `mcp:raw-api` without claiming they are harmless or
   that they necessarily grant administrator access.
3. Let the owner explicitly accept that exact grant or cancel. No connection,
   available tools, capability approval, or job access exists before acceptance.
   Acceptance records a deliberate exception for that connection, not a
   global Grafana policy. Owners unwilling to accept the unknown scope
   semantics must be able to leave the connection disconnected.
4. After acceptance, discover tools and apply the existing tool-selection and
   job approval rules. Discovery must still succeed before the connection is
   saved. If the owner selects only read-only tools, preserve that restriction;
   tool selection does not narrow the OAuth token's underlying permissions.

This is a proposed consent mechanism, not a claim that the added permissions
are safe. Before implementation, review whether explicitly accepting these
unknown permissions is an acceptable product policy. If not, retain the
current rejection until Grafana documents or fixes the scope behavior.

## Trust and token handling

- Match parsed HTTPS origin and endpoint paths exactly for the official
  `mcp.grafana.com` service. Verify the discovered issuer, resource, and token
  endpoint belong to that service. Never recognize Grafana by a display name,
  binding key, substring, suffix match, or arbitrary custom-server metadata.
- Store pending tokens encrypted in a short-lived server-side record bound to
  the initiating user, workspace, OAuth attempt, resource, and issuer. Keep
  them out of query strings, browser storage, API responses, and logs.
- Make acceptance a single-use, CSRF-protected action by a workspace owner
  with current connection permissions. Recheck permissions and workspace
  membership at acceptance time, not only at OAuth start.
- Expire and purge pending tokens on timeout or cancellation. Failed discovery
  must not leave a usable connection. Do not claim deleting a local token
  revokes Grafana's remote authorization; document remote revocation separately.
- Persist requested scopes, actual granted scopes, and the exact explicitly
  approved additions separately. Keep the actual grant visible to owners.
- Apply the same scope policy on token refresh. A refreshed token must not
  introduce new scope strings beyond the approved grant. Preserve permitted
  downscoping; do not treat reconnecting as consent to broaden access.

## Alternatives

A provider-specific normalization rule could avoid the review step if Grafana
documents that the extra scopes do not expand the requested authorization.
That would require authoritative semantics, fixtures for Read/Query/Write,
and exact service and scope matching. Current observations are insufficient
to justify such a rule. Do not ship it based on scope names alone.

Registering a manual OAuth client is not currently a drop-in workaround:
SecondShift's MCP flow registers public PKCE clients dynamically and rejects
client-secret registration. Do not promise that a service-account token will
authenticate to the hosted Cloud MCP endpoint either; Grafana's separate OSS
MCP server supports that deployment model.

## Required verification before implementation ships

- Mock the observed read-only token response and complete owner review before
  a connection is saved; verify retained scopes and selected tools.
- Reject unrequested Query/Write scopes, unknown additions, lookalike hosts,
  mismatched issuers/resources/token endpoints, and equivalent responses from
  non-Grafana servers. Existing generic scope rejection must remain intact.
- Test owner denial, expiry, replay, cross-user/workspace access, lost
  permissions, CSRF protection, discovery failure, and secret redaction.
- Test refresh and reconnect for unchanged, reduced, and expanded grants.
- Validate the flow with a real Grafana Cloud stack using Read only. Compare
  metadata, consent, returned scopes, and available tools without retaining
  tokens or authorization codes in fixtures or logs.

This proposal is independent of local JSON Schema reference support. Once
scope handling succeeds, tool discovery may still encounter that separate
compatibility issue.
