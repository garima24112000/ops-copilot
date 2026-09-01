variable "elasticsearch_endpoint" {
  description = "Elasticsearch URL (matches ELASTICSEARCH_URL in .env)"
  type        = string
  default     = "http://localhost:9200"
}

variable "elastic_password" {
  description = "elastic superuser password (matches ELASTIC_PASSWORD in .env). Set via TF_VAR_elastic_password, never committed."
  type        = string
  sensitive   = true
}

variable "elser_inference_id" {
  description = "Inference endpoint id for ELSER, matching scripts/deploy_elser.py's default."
  type        = string
  default     = "ops-copilot-elser"
}
