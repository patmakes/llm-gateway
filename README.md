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
