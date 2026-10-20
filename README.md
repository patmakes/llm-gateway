# llm-gateway

nginx gateway (Docker) in front of llama-server and a standalone MCP VM.

Both upstreams sit behind one origin on the gateway, so the browser talks to a
single host and certificate:

    https://llm.example.ts.net/      -> llama-server (native TLS :8443)
    https://llm.example.ts.net/mcp   -> MCP host :8000 (web_search via SearXNG, web_fetch)

## Flow

Clients reach **only** the gateway. Both upstreams sit behind it, each accepting
the gateway's IP and nothing else, so there is no path that skips the front door.

                                                                  ┌────────────────────────────────┐
    ┌──────────────────┐                                          │ llm-host                       │
    │ Browser          │        ┌────────────────────────────┐    │                                │
    │                  │        │ gateway                    │ /  │ llama-server :8443             │
    │ llama.cpp web UI │────┐   │ nginx :443 (Docker)        │───▶│ native TLS + API key           │
    │ + in-browser     │    │   │                            │    │ RTX 3060 12 GB, systemd        │
    │   MCP client     │    │   │ TLS terminate, own CA      │    │ ufw: gateway IP only           │
    └──────────────────┘    ├──▶│ allow/deny client CIDRs    │    └────────────────────────────────┘
                            │   │ bearer token on /mcp       │
                            │   │ verifies upstream certs    │
    ┌──────────────────┐    │   │ per-request DNS: one dead  │──┐ /mcp
    │ OpenCode         │────┘   │ VM = 502 on its path only  │  │
    └──────────────────┘        └────────────────────────────┘  │
                                                                │ ┌────────────────────────────────┐
                                                                │ │ mcp-host   (untrusted zone)    │
                                                                │ │                                │
                                                                │ │ MCP web-tools :8000            │
                                                                │ │ Streamable HTTP, stateless     │
                                                                └▶│ SSRF-checked web_fetch         │
                                                                  │ ufw: gateway IP only           │
                                                                  │                                │
                                                                  │ SearXNG (no published port)    │
                                                                  │                                │
                                                                  │ nftables egress: 80/443 +      │
                                                                  │ DNS only, nothing private      │
                                                                  └────────────────────────────────┘

**llama-server never calls the MCP server.** The *client* holds the MCP
connection: the web UI lists the tools, hands them to the model, and issues
`tools/call` itself when the model asks for one. Both paths land on the gateway,
on one origin — which is why the in-browser MCP client needs no CORS and
"Use llama-server proxy" stays off.

`mcp-host` is the untrusted-content zone: it reads arbitrary web pages, and a
page can tell the model to fetch `http://192.168.0.1`. So it has no network
route to anything private — enforced by nftables, not just by the application's
address check — and `mcp-vm.yml` fails its own run if that guard ever stops
working.

## Layout

- `mcp-server/` — Python MCP server (Streamable HTTP, stateless) + Dockerfile
- `nginx/llm-gateway.example.conf` — rendered gateway config for reading
- `ansible/` — `mcp-vm.yml`, `nginx-gateway.yml`, templates, vars
- `examples/` — llm-host's systemd unit and launch script (that host is set up
  by hand, not by Ansible)

## Deploy

1. Put `ca-root.pem`, `gateway.crt/.key`, `mcp.crt/.key` in `ansible/files/tls/`
   (SANs: gateway = `gateway_fqdn`, mcp = `mcp_upstream_host`).
2. Edit `ansible/inventory.ini` and `ansible/group_vars/all.yml`
   (pin `searxng_image`, vault `mcp_bearer_token`).
3. Run:

        cd ansible
        ansible-galaxy collection install community.docker
        ansible-playbook -i inventory.ini mcp-vm.yml
        ansible-playbook -i inventory.ini nginx-gateway.yml

## Starting llama-server on llm-host

`llm-host` is **not** Ansible-managed — it owns the GPU and is set up by hand,
so these two files are its whole deployment:

| File | Role |
| --- | --- |
| [`examples/start-llm.sh.example`](examples/start-llm.sh.example) | Launch script: the tuned flags, with paths as four variables at the top |
| [`examples/llama-server.service.example`](examples/llama-server.service.example) | systemd unit that runs it |

    sudo install -m 0755 examples/start-llm.sh.example /usr/local/bin/start-llm.sh
    sudo cp examples/llama-server.service.example /etc/systemd/system/llama-server.service
    sudo systemctl daemon-reload && sudo systemctl enable --now llama-server
    journalctl -u llama-server -f

Three things in there that are deliberate:

- **`exec llama-server`** on the last line of the script, so the shell is
  *replaced* by the server and systemd's stop/restart signals reach it directly
  instead of dying in a wrapper.
- **`TimeoutStartSec=600`**, because loading ~25 GiB with `--load-mode none`
  takes long enough that the default start timeout calls it a failure.
- **`Restart=on-failure` plus `enable`**, because a reboot once left the server
  simply not running and the gateway returned 504s until someone noticed.

Before it starts, generate the API key the unit expects and confirm no other
runtime is holding VRAM:

    (umask 077; openssl rand -hex 32 | sudo tee /etc/llama/api_key >/dev/null)
    ollama ps        # keep_alive: -1 parks ~10 GB indefinitely; ollama stop <model>

The host firewall must accept 8443 from the gateway only; `--api-key-file` is
the second gate, so `/v1/models` returns 401 without the key (`/health` stays
open).

## Test the MCP endpoint by hand

    H=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H "Authorization: Bearer $MCP_TOKEN")
    curl -s https://llm.<tailnet>/mcp "${H[@]}" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

## Run the MCP server locally

    pip install -r mcp-server/requirements.txt
    SEARXNG_URL=http://localhost:8080 python mcp-server/server.py   # serves http://0.0.0.0:8000/mcp

## License

This repository — the Ansible playbooks, the MCP server, the nginx template and
the docs — is [MIT licensed](LICENSE).

### Licensing of components

The MIT license covers this repo's own code. Everything it deploys or depends on
keeps its own terms, listed here so you don't have to go looking:

| Component | License | Notes |
| --- | --- | --- |
| This repo | MIT | Playbooks, `mcp-server/`, nginx template, docs |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) (`llama-server`) | MIT | Built from source on the LLM host; not redistributed here |
| [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk) (`mcp`) | MIT | Pulled via `requirements.txt` |
| [httpx](https://github.com/encode/httpx), [uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | Pulled via `requirements.txt` |
| [trafilatura](https://github.com/adbar/trafilatura) | Apache-2.0 | Readability extraction in `web_fetch` |
| [SearXNG](https://github.com/searxng/searxng) | AGPL-3.0 | Deployed as an **unmodified upstream image**; no source changes are made or distributed |
| [nginx](https://nginx.org/LICENSE) | BSD-2-Clause | Deployed as an unmodified upstream image |
| [Ansible](https://github.com/ansible/ansible), `community.docker` | GPL-3.0 | Control-node tooling only; not distributed |

**Model weights are licensed separately from this repo.** The deployment in the
docs uses [`bartowski/North-Mini-Code-1.0-GGUF`](https://huggingface.co/bartowski/North-Mini-Code-1.0-GGUF)
(Apache-2.0), quantized from
[`CohereLabs/North-Mini-Code-1.0`](https://huggingface.co/CohereLabs/North-Mini-Code-1.0)
(Apache-2.0). No weights are included in or redistributed by this repo — you
download them yourself. If you swap in a different model, check its card: plenty
of open-weight models ship under non-commercial or acceptable-use terms rather
than a permissive license.
