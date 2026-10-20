# llm-gateway

nginx gateway (Docker) in front of llama-server and a standalone MCP VM.

    https://llm.<tailnet>/      -> llama-server (native TLS :8443)
    https://llm.<tailnet>/mcp   -> MCP VM :8000 (web_search via SearXNG, web_fetch)

## Layout

- `mcp-server/` — Python MCP server (Streamable HTTP, stateless) + Dockerfile
- `nginx/llm-gateway.example.conf` — rendered gateway config for reading
- `ansible/` — `mcp-vm.yml`, `nginx-gateway.yml`, templates, vars

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
