provider "elasticstack" {
  elasticsearch {
    endpoints = [var.elasticsearch_endpoint]
    username  = "elastic"
    password  = var.elastic_password
  }
}

# Configured so the "IaC that could also stand up the containers" story is real and pinned to
# a provider version, without fighting docker-compose for ownership of the same containers --
# docker-compose.yml stays authoritative for the running stack (session 1's gate). This reads
# the network compose already created rather than declaring competing resources for it.
provider "docker" {}

data "docker_network" "ops_copilot" {
  name = "ops-copilot_default"
}

# --- Index templates -------------------------------------------------------------------

resource "elasticstack_elasticsearch_index_template" "ops_runbooks" {
  name           = "ops-runbooks-template"
  index_patterns = ["ops-runbooks"]

  template {
    settings = jsonencode({
      number_of_shards   = 1
      number_of_replicas = 0
    })
    mappings = jsonencode({
      properties = {
        id            = { type = "keyword" }
        title         = { type = "text", fields = { keyword = { type = "keyword" } } }
        body          = { type = "text" }
        body_semantic = { type = "semantic_text", inference_id = var.elser_inference_id }
        body_dense    = { type = "dense_vector", dims = 384, index = true, similarity = "cosine" }
        service       = { type = "keyword" }
        department    = { type = "keyword" }
        source_url    = { type = "keyword" }
      }
    })
  }
}

resource "elasticstack_elasticsearch_index_template" "ops_incidents" {
  name           = "ops-incidents-template"
  index_patterns = ["ops-incidents"]

  template {
    settings = jsonencode({
      number_of_shards   = 1
      number_of_replicas = 0
    })
    mappings = jsonencode({
      properties = {
        id                 = { type = "keyword" }
        title              = { type = "text", fields = { keyword = { type = "keyword" } } }
        summary            = { type = "text" }
        summary_semantic   = { type = "semantic_text", inference_id = var.elser_inference_id }
        resolution         = { type = "text" }
        service            = { type = "keyword" }
        department         = { type = "keyword" }
        severity           = { type = "keyword" }
        related_runbook_id = { type = "keyword" }
        created_at         = { type = "date" }
      }
    })
  }
}

resource "elasticstack_elasticsearch_index_template" "ops_logs" {
  name           = "ops-logs-template-tf"
  index_patterns = ["ops-logs-*"]

  data_stream {}

  template {
    settings = jsonencode({
      number_of_shards   = 1
      number_of_replicas = 0
    })
    mappings = jsonencode({
      properties = {
        "@timestamp" = { type = "date" }
        service      = { type = "keyword" }
        level        = { type = "keyword" }
        message      = { type = "text" }
      }
    })
  }
}

resource "elasticstack_elasticsearch_index_template" "ops_agent_evals" {
  name           = "ops-agent-evals-template"
  index_patterns = ["ops-agent-evals"]

  template {
    settings = jsonencode({
      number_of_shards   = 1
      number_of_replicas = 0
    })
    mappings = jsonencode({
      properties = {
        run_id       = { type = "keyword" }
        eval_type    = { type = "keyword" }
        strategy     = { type = "keyword" }
        subset       = { type = "keyword" }
        metric       = { type = "keyword" }
        score        = { type = "float" }
        git_sha      = { type = "keyword" }
        "@timestamp" = { type = "date" }
      }
    })
  }
}

# --- ELSER inference endpoint ------------------------------------------------------------
# Deliberately a separate, Terraform-owned id rather than the preconfigured
# `.elser-2-elasticsearch` that scripts/deploy_elser.py uses at runtime (see that script's
# docstring): a fresh ES 9.x cluster already ships that one, so there is nothing for Terraform
# to legitimately "create" there without an import step. This resource demonstrates the same
# capability -- inference endpoints as code -- without an ownership conflict.

resource "elasticstack_elasticsearch_inference_endpoint" "ops_copilot_elser" {
  inference_id = "ops-copilot-elser-tf"
  task_type    = "sparse_embedding"
  service      = "elasticsearch"
  service_settings = jsonencode({
    num_allocations = 1
    num_threads     = 1
    model_id        = ".elser_model_2"
  })
}

# --- Document-level security: roles + API keys, one per department ----------------------

variable "departments" {
  description = "Fixed department list, matching ingest/fetch_corpus.py's DEPARTMENTS and security/dls.py's DEMO_USERS."
  type        = list(string)
  default = [
    "platform-engineering",
    "database-reliability",
    "networking",
    "security-compliance",
    "observability",
  ]
}

resource "elasticstack_elasticsearch_security_role" "ops_copilot_department" {
  for_each = toset(var.departments)
  name     = "ops-copilot-${each.value}"

  indices {
    names      = ["ops-runbooks"]
    privileges = ["read"]
    query      = jsonencode({ term = { department = each.value } })
  }
  indices {
    names      = ["ops-incidents"]
    privileges = ["read"]
  }
  indices {
    names      = ["ops-logs-*"]
    privileges = ["read"]
  }
  indices {
    names      = ["ops-postmortems"]
    privileges = ["read", "write", "create_index"]
  }
}

resource "elasticstack_elasticsearch_security_api_key" "ops_copilot_department" {
  for_each = toset(var.departments)
  name     = "ops-copilot-tf-${each.value}"
  role_descriptors = jsonencode({
    (elasticstack_elasticsearch_security_role.ops_copilot_department[each.value].name) = {
      indices = [
        {
          names      = ["ops-runbooks"]
          privileges = ["read"]
          query      = { term = { department = each.value } }
        },
        { names = ["ops-incidents"], privileges = ["read"] },
        { names = ["ops-logs-*"], privileges = ["read"] },
        { names = ["ops-postmortems"], privileges = ["read", "write", "create_index"] },
      ]
    }
  })
}
