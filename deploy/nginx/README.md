# nginx TLS

`agent.conf` expects:

- `deploy/nginx/certs/agent.crt`
- `deploy/nginx/certs/agent.key`

For production, use an internal CA certificate issued by the organization. Do not commit private keys.

For a lab-only smoke test:

```bash
mkdir -p deploy/nginx/certs
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout deploy/nginx/certs/agent.key \
  -out deploy/nginx/certs/agent.crt \
  -days 30 \
  -subj "/CN=tekla-ai.local"
```

