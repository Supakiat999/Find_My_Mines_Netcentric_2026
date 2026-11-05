data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_cloudwatch_log_group" "game" {
  name              = "/${var.project}/${var.environment}/game"
  retention_in_days = var.log_retention_days

  tags = { Name = "${local.name}-game-logs" }
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name               = "${local.name}-host"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json

  tags = { Name = "${local.name}-host" }
}

resource "aws_iam_role_policy_attachment" "ecr_read" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

data "aws_iam_policy_document" "host" {
  statement {
    sid     = "ReadBootstrapParameters"
    actions = ["ssm:GetParameter"]
    resources = [
      aws_ssm_parameter.database_url.arn,
      aws_ssm_parameter.image_uri.arn,
    ]
  }

  statement {
    sid       = "DecryptBootstrapParameters"
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "ReadRdsMasterSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_db_instance.main.master_user_secret[0].secret_arn]
  }

  statement {
    sid = "WriteGameLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:DescribeLogStreams",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.game.arn}:*"]
  }
}

resource "aws_iam_role_policy" "host" {
  name   = "${local.name}-host"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.host.json
}

resource "aws_iam_instance_profile" "host" {
  name = "${local.name}-host"
  role = aws_iam_role.host.name
}

resource "aws_key_pair" "host" {
  key_name   = "${local.name}-host"
  public_key = trimspace(file(pathexpand(var.ssh_public_key_path)))

  tags = { Name = "${local.name}-host" }
}

resource "aws_instance" "host" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public[0].id
  vpc_security_group_ids      = [aws_security_group.game.id]
  iam_instance_profile        = aws_iam_instance_profile.host.name
  key_name                    = aws_key_pair.host.key_name
  associate_public_ip_address = true

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  # t3/t4g default to unlimited CPU credits, which can add a surprise charge.
  dynamic "credit_specification" {
    for_each = can(regex("^t[234]", var.instance_type)) ? [1] : []

    content {
      cpu_credits = "standard"
    }
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    encrypted             = true
    delete_on_termination = true
  }

  # The host reads the runtime URL and image tag from SSM at boot, so changing
  # image_tag only updates the parameter. Run `make bootstrap` to apply it.
  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    aws_region     = var.aws_region
    image_param    = aws_ssm_parameter.image_uri.name
    database_param = aws_ssm_parameter.database_url.name
    rds_secret_arn = aws_db_instance.main.master_user_secret[0].secret_arn
    db_host        = aws_db_instance.main.address
    db_name        = aws_db_instance.main.db_name
    log_group      = aws_cloudwatch_log_group.game.name
    advertise_host = aws_eip.host.public_ip
    compose_file   = filebase64("${path.module}/../../compose.aws.yaml")
  })

  depends_on = [
    aws_db_instance.main,
    aws_ssm_parameter.database_url,
    aws_ssm_parameter.image_uri,
    aws_cloudwatch_log_group.game,
    aws_iam_role_policy.host,
  ]

  tags = { Name = "${local.name}-host" }
}

# Allocate the address before the instance so user-data can advertise the
# public host in the browser check and the admin console.
resource "aws_eip" "host" {
  domain = "vpc"

  tags = { Name = "${local.name}-host" }
}

resource "aws_eip_association" "host" {
  allocation_id = aws_eip.host.id
  instance_id   = aws_instance.host.id
}
