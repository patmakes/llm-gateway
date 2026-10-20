# Security model

What this deployment defends against, which control does the defending, and what
it still doesn't cover. Every control below points at the file that implements
it — if a row has no file, it isn't a control, it's an intention.

Scope: the three VMs in this repo (`gateway`, `llm-host`, `mcp-host`) and the
clients that use them. Out of scope: how the hosts themselves are provisioned,
how service accounts are created, and SSH key handling. The only prerequisite
this repo states is an SSH user with sudo on each host, named in `inventory.ini`.

## Trust boundaries

Four zones, ordered from most to least trusted:

| Zone | Contains | Trusted to |
| --- | --- | --- |
| **Client network** | Browsers, OpenCode, over Tailscale or the LAN | Hold the llama-server API key and the `/mcp` bearer token. Compromise here means full use of the model. |
| **Gateway** | nginx, no state | Terminate client TLS, decide who may connect, and originate connections to both upstreams. The only host clients can reach. |
| **llm-host** | llama-server, the GPU, the model file | Run inference. Reachable only from the gateway. Holds no client data beyond the conversation in flight. |
| **mcp-host** | MCP server, SearXNG | **Nothing.** This is the untrusted-content zone. |

The last row is the one that shapes the design. `mcp-host` fetches arbitrary web
pages on behalf of a language model, and the text of those pages lands in the
model's context where it is indistinguishable from the user's own instructions.
Any page can therefore attempt to issue instructions. So `mcp-host` is treated
as though it is already compromised: it holds no credentials worth stealing, no
state that isn't rebuildable from Ansible, and — the part that matters — no
network route to anything else.

The boundary is not "the MCP server behaves correctly." It is the egress
firewall in `ansible/templates/mcp-guard.nft.j2`, which holds whether or not the
application does.

## Assets worth protecting

1. **The LAN and tailnet.** Hosts, admin interfaces, other self-hosted services.
2. **Conversation content.** Logs, config and code pasted into the model.
3. **Credentials.** The llama-server API key, the `/mcp` bearer token, the
   SearXNG secret, the private CA and its issued keys.
4. **Availability of inference.** Lower stakes, but a gateway that won't start
   because one VM is off is a real failure mode.

## Threats and controls

| # | Threat | Control | Where |
| --- | --- | --- | --- |
| 1 | **Exposure to the internet.** Any of this reachable from outside | Nothing is port-forwarded. Clients arrive over Tailscale or the LAN only; nginx additionally enforces an `allow`/`deny` client CIDR list ending in `deny all` | `llm-gateway.conf.j2:39-41` |
| 2 | **Direct access to llm-host, bypassing the gateway** | Host firewall accepts 8443 from the gateway address only; `--api-key-file` is a second, independent gate, so a network mistake alone is not sufficient | `examples/start-llm.sh.example`, host ufw (set up by hand) |
| 3 | **Direct access to the MCP server, bypassing the gateway** | Published port 8000 is dropped for every source but the gateway. Because Docker DNATs published ports, this lives in the `forward` chain, not `input` | `mcp-guard.nft.j2:31` |
| 4 | **Lateral movement from a compromised MCP container** | Container egress is restricted to TCP 80/443 plus DNS to one resolver. Every private range — RFC1918, loopback, link-local, multicast, and CGNAT `100.64.0.0/10` for Tailscale — is dropped, with a final `drop` catching all other protocols and ports | `mcp-guard.nft.j2:36-39` |
| 5 | **SSRF via prompt-injected fetch.** A page tells the model to fetch `http://192.168.0.1` | Two independent layers. **App:** resolves the hostname and refuses non-public addresses, restricts scheme and port, re-checks on *every* redirect hop, caps bytes and redirect count. **Network:** the egress rules in row 4, which hold even if the app check is wrong | `server.py:109-152`; `mcp-guard.nft.j2:36-39` |
| 6 | **DNS rebinding.** DNS answers differently between the app's check and the actual connect | The app check cannot win this race, by construction. The egress firewall does not care what a name resolved to — it filters the packet's destination address | `mcp-guard.nft.j2:36-37` |
| 7 | **DNS rebinding against the MCP server itself.** A browser is tricked into posting to `/mcp` | The SDK's transport security rejects requests whose `Host` is not in `allowed_hosts`, enabled whenever `MCP_ALLOWED_HOSTS` is set; `allowed_origins` is derived from the same list | `server.py:43-51` |
| 8 | **Credential theft from the repo** | Secrets live in `ansible-vault`-encrypted `vault.yml`; `.gitignore` excludes `vault*.yml`, `all.yml`, `inventory.ini`, `.env`, and every `*.key`/`*.pem`/`*.crt`. Only `*.example` files are committed | `.gitignore`, `group_vars/vault.example.yml` |
| 9 | **Model-triggered dangerous tools.** The model is talked into running a shell command or writing a file | llama-server's built-in tool runtime is simply not enabled: no `--tools all`, no `--agent` in the launch flags. The only tools that exist are `web_search` and `web_fetch`, neither of which writes anything | `examples/start-llm.sh.example` |
| 10 | **Impersonation of an upstream on the internal network** | The gateway verifies both upstream certificates against the private CA, at a bounded depth, with `proxy_ssl_name` pinned to each upstream's SAN. "It's just the LAN" is not the trust model | `llm-gateway.conf.j2:47-51` |
| 11 | **Container breakout on either host** | Read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`, non-root UID 10001, memory and PID limits; SearXNG has no published port and is reachable only by the MCP container | `compose.yml.j2:14-16,37-38,51`; `mcp-server/Dockerfile:7,10`; `nginx-gateway.yml:89-92` |
| 12 | **Unauthenticated use of the tool server** | Optional bearer token on `/mcp`, checked by an nginx `map`, returning 401 on mismatch. The token is stripped before the upstream call, so it never reaches the MCP server | `llm-gateway.conf.j2:14,63,71` |
| 13 | **Supply chain** | Images pinned by tag (`nginx:1.29-alpine`, `searxng_image` must be pinned by the operator); Docker installed from its own signed apt repo; the `mcp` SDK is bounded to its major version (`mcp>=1.20,<2`) | `group_vars/all.example.yml`, `tasks-docker.yml`, `requirements.txt` |
| 14 | **Misconfiguration taking the service down** | nftables rules are parse-checked (`nft -c -f`) before loading; nginx config is validated in a throwaway container *before* the live container is touched; upstreams resolve per request, so one dead VM is a 502 on its own path rather than a gateway that won't start | `mcp-vm.yml:82`; `nginx-gateway.yml:63`; `llm-gateway.conf.j2` resolver block |
| 15 | **A control silently stopping working** | The MCP playbook ends by opening a connection from inside the container to the gateway's address and **failing the run if it succeeds** | `mcp-vm.yml:112-118` |

## Residual risks

Stated plainly, because a security model that claims no gaps isn't one.

**Prompt injection still works on the model.** Every control above limits what
an injected instruction can *reach*. None of them limit what the model *says*.
A hostile page can still talk the model into a wrong, confident answer, or into
summarising its own content misleadingly. If you act on what it tells you, that
is the exposure.

**The `/mcp` bearer token is optional.** `mcp_bearer_token` defaults to empty,
which disables the check entirely and leaves `/mcp` protected by network
controls alone. Any client on the tailnet or LAN can then use the tools.

**Root on mcp-host can still reach private networks.** The output chain drops
private destinations only for `skuid != 0` (`mcp-guard.nft.j2:49`), so a root
process on the VM itself is not contained by it. Container traffic — the actual
threat surface — goes through the `forward` chain and is fully filtered.

**The client allowlist is IPv4-only.** `gateway_allowed_client_cidrs` ships with
IPv4 ranges, so a client arriving over IPv6 hits `deny all` and gets a 403 that
reads like an authentication failure. Adding an IPv6 range fixes the symptom and
widens the allowlist — know which you're doing.

**Search queries leave the network.** SearXNG means no account, no single
provider building a profile, and query fan-out across engines. It does not mean
anonymous: the queries themselves still reach those engines from your IP.

**The web UI stores the API key in the browser.** Anyone with access to that
browser profile has the key. There is one shared key, so there is no per-user
identity and no way to revoke one client without rotating for all of them.

**No revocation checking on the private CA.** `proxy_ssl_verify` validates the
chain; it does not consult a CRL. A compromised upstream key stays trusted until
the CA material is rotated by hand.

**Pinning is partial.** Images are pinned by tag, not digest, so a tag can be
republished. `mcp>=1.20,<2` bounds a major version rather than locking a
version, and the other three dependencies (`httpx`, `trafilatura`, `uvicorn`)
carry a floor with **no upper bound at all** — a new major release of any of
them lands in the next image build. There is no lock file. `searxng_image` ships
as a placeholder the operator must fill in, and an unpinned value there is a
supply-chain hole of your own making.

**The smoke tests run at deploy time only.** Row 15 proves the egress guard
works when the playbook runs. Nothing re-checks it afterwards, and an nftables
reload that loses Docker's chains is exactly the kind of event that happens
between deploys.

**Trusted clients are fully trusted.** Anyone who can reach the gateway with the
API key has the whole model. There is no rate limiting, no quota, and no audit
trail beyond nginx's access log.

## Verifying it

The controls above are testable, and most of them fail open if you get them
wrong — so test them rather than assuming:

| Claim | Test | Expect |
| --- | --- | --- |
| llm-host is gateway-only | `curl https://<llm-host>:8443/health` from a laptop | Times out |
| mcp-host is gateway-only | `curl https://<mcp-host>:8000/mcp` from a laptop | Times out |
| API key is enforced | `curl https://<gateway>/v1/models` with and without the key | 401, then 200 |
| `/mcp` token is enforced | POST `/mcp` without `Authorization` | 401 (when the token is set) |
| SSRF app check works | `web_fetch` of `http://192.168.0.1` | `Refusing to fetch non-public address ...` |
| SSRF network control works | From inside the MCP container, connect to the gateway's address | Fails at the network level |
| One dead VM isn't fatal | `docker restart nginx-gateway` with mcp-host powered off | Starts; `/mcp` 502s; chat still works |
