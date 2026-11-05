output "server_host" {
  description = "Elastic IP clients connect to."
  value       = aws_eip.host.public_ip
}

output "server_port" {
  description = "Game TCP port."
  value       = 55550
}

output "client_command" {
  description = "Run this on a player's computer."
  value       = "python client.py ${aws_eip.host.public_ip}"
}

output "browser_check" {
  description = "Open this to prove the network path works."
  value       = "http://${aws_eip.host.public_ip}:55550/"
}

output "ssh_key_path" {
  description = "Absolute path to the local private key matching the registered public key."
  value       = pathexpand(trimsuffix(var.ssh_public_key_path, ".pub"))
}

output "ssh_command" {
  description = "Shell into the host."
  value       = "ssh -i ${pathexpand(trimsuffix(var.ssh_public_key_path, ".pub"))} ec2-user@${aws_eip.host.public_ip}"
}

output "ecr_repository_url" {
  description = "ECR repository for the game image."
  value       = aws_ecr_repository.app.repository_url
}

output "ecr_image" {
  description = "Image URI the host pulls."
  value       = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
}

output "aws_region" {
  description = "Region in use."
  value       = var.aws_region
}

output "rds_endpoint" {
  description = "RDS endpoint. Not reachable from outside the VPC."
  value       = aws_db_instance.main.endpoint
}

output "cloudwatch_log_group" {
  description = "Container log group."
  value       = aws_cloudwatch_log_group.game.name
}
