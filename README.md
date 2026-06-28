# loan-agent

FastAPI loan agent that calls the FD Agent through agentgateway with a
workload identity token. Demonstrates RBAC enforcement — the loan-agent
identity is restricted to read-only FD operations by agentgateway policy.

## Usage

```bash
make build IMAGE_REPO=australia-southeast1-docker.pkg.dev/field-engineering-apac/kasunt
make push  IMAGE_REPO=australia-southeast1-docker.pkg.dev/field-engineering-apac/kasunt
make deploy
```
