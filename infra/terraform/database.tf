resource "random_password" "runtime" {
  length  = 32
  special = false
}

resource "aws_ssm_parameter" "database_url" {
  name        = "/${var.project}/${var.environment}/database-url"
  description = "Restricted runtime URL used by the game server."
  type        = "SecureString"
  value       = "postgresql://runtime:${random_password.runtime.result}@${aws_db_instance.main.address}:5432/${aws_db_instance.main.db_name}"

  tags = { Name = "${local.name}-database-url" }
}

resource "aws_ssm_parameter" "image_uri" {
  name        = "/${var.project}/${var.environment}/image-uri"
  description = "Container image the host runs. Update this, then re-run bootstrap."
  type        = "String"
  value       = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"

  tags = { Name = "${local.name}-image-uri" }
}

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id

  tags = { Name = local.name }
}

resource "aws_db_instance" "main" {
  identifier = local.name

  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  db_name  = "find_my_mines"
  username = "mines_admin"

  # RDS creates and rotates the master password in Secrets Manager. Terraform
  # never handles that secret value.
  manage_master_user_password = true

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_allocated_storage * 2
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false

  multi_az                   = false
  backup_retention_period    = var.db_backup_retention_days
  auto_minor_version_upgrade = true
  copy_tags_to_snapshot      = true
  apply_immediately          = true

  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = var.db_skip_final_snapshot
  final_snapshot_identifier = var.db_skip_final_snapshot ? null : "${local.name}-final"

  tags = { Name = local.name }
}
