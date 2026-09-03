output "rds_endpoint" {
  description = "PostgreSQL RDS endpoint"
  value       = aws_db_instance.postgres.endpoint
  sensitive   = true
}

output "redis_endpoint" {
  description = "ElastiCache Redis endpoint"
  value       = aws_elasticache_cluster.redis.cache_nodes[0].address
}

output "s3_documents_bucket" {
  description = "S3 bucket for documents"
  value       = aws_s3_bucket.documents.bucket
}

output "sqs_ingestion_url" {
  description = "SQS ingestion queue URL"
  value       = aws_sqs_queue.ingestion.url
}

output "eks_cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = aws_eks_cluster.main.endpoint
  sensitive   = true
}

output "ecr_fastapi_url" {
  description = "ECR repository URL for FastAPI image"
  value       = aws_ecr_repository.fastapi.repository_url
}

output "ecr_enterprise_api_url" {
  description = "ECR repository URL for Enterprise API image"
  value       = aws_ecr_repository.enterprise_api.repository_url
}
