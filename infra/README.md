# Terraform: index templates, ELSER endpoint, DLS roles + API keys as code

Manages the Elasticsearch-side configuration of ops-copilot declaratively, against the
**local** cluster from `docker-compose.yml` -- there is no cloud deployment here, which the
plan argues is actually the more relevant story for an internal IT platform team than
provisioning a hosted cluster.

## What's managed here vs. elsewhere

- **Index templates** (ops-runbooks, ops-incidents, ops-logs-*, ops-agent-evals): mirrors
  `ingest/mappings/*.json`, which `ingest/load.py` still uses directly for the actual
  create-index calls (idempotent recreate-per-run, needed for `make ingest` to stay simple).
  This module exists to show the templates *could* be the terraform-managed source of truth.
- **ELSER inference endpoint**: a separate, Terraform-owned endpoint id
  (`ops-copilot-elser-tf`), not the preconfigured `.elser-2-elasticsearch` that
  `scripts/deploy_elser.py` uses at runtime -- a fresh ES 9.x cluster ships that one
  preconfigured, so there's nothing for Terraform to legitimately create there without an
  import step first. This resource proves the same capability without an ownership conflict.
- **DLS roles + API keys**: one role + one API key per department in `var.departments`,
  restricting `ops-runbooks` by the `department` field -- the Terraform-native version of what
  `security/dls.py` does at runtime for the demo users. In practice `security/dls.py` is what
  the CLI's `--user` flag actually uses (it mints keys on demand and caches them locally);
  this module is the "as code" version of the same DLS pattern.
- **Containers**: `docker-compose.yml` remains authoritative (session 1's gate). The `docker`
  provider is configured and reads the network compose already created, rather than declaring
  competing container resources docker-compose already owns.

## Apply against your local stack

```bash
cd infra
terraform init
TF_VAR_elastic_password=$ELASTIC_PASSWORD terraform plan
TF_VAR_elastic_password=$ELASTIC_PASSWORD terraform apply
```

Requires the stack to be up (`make up`) and, for the index templates, a healthy cluster.
`TF_VAR_elastic_password` is read from your shell -- never hardcode it in a `.tfvars` file
that could get committed.

## Known limitation

API keys minted via `elasticstack_elasticsearch_security_api_key` cannot be read back once
created (Elasticsearch never returns the secret again after creation) -- Terraform tracks the
key's *existence*, not its value, the same way `security/dls.py`'s cache file works. A
`terraform destroy` + `apply` cycle mints fresh keys; there is no way to "reveal" an old one.
