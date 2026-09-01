terraform {
  required_version = ">= 1.5"
  required_providers {
    elasticstack = {
      source  = "elastic/elasticstack"
      version = ">= 0.16.0, < 1.0.0"
    }
    docker = {
      source  = "kreuzwerker/docker"
      version = ">= 3.0.0, < 5.0.0"
    }
  }
}
