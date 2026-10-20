# TLS material (not committed)

Everything in this folder except this README is git-ignored. Issue these from
your own CA (e.g. easy-rsa) and drop them here before running the playbooks:

| File          | Used by  | Notes                                                |
| ------------- | -------- | ---------------------------------------------------- |
| `ca.crt`      | gateway  | Root CA (PEM). Deployed as `ca-root.pem` on the gateway |
| `gateway.crt` | gateway  | SAN = `gateway_fqdn`                                  |
| `gateway.key` | gateway  | Private key, mode 0600                                |
| `mcp.crt`     | MCP host | SAN = `mcp_upstream_host`                             |
| `mcp.key`     | MCP host | Private key, mode 0600                                |

The llama-server host gets its own cert (SAN = `llama_upstream_host`),
installed by hand alongside its systemd unit.

Check a file is PEM before use: `head -1 ca.crt` should print
`-----BEGIN CERTIFICATE-----`. A DER file needs
`openssl x509 -inform der -in ca.crt -out ca.crt.pem`.
