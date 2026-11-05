# Deploying Find My Mines

Two ways to run the server in a container:

- **Local full stack** — `compose.yaml` with the `game` profile.
- **AWS** — one EC2 host running `compose.aws.yaml`, backed by RDS PostgreSQL.

The client is still a desktop pygame app. Only the server is deployed.

---

## Local container

```bash
cp .env.example .env           # set POSTGRES_PASSWORD and RUNTIME_PASSWORD
docker compose --profile game up -d --build --wait
curl http://127.0.0.1:55550/   # "Connection works"
```

`docker compose up` without the profile still starts only PostgreSQL and
Adminer, exactly as before. Add `INSTALL_TORCH=true` to the build to enable
the trained bots in `bot/weights`; otherwise `botbrain` uses the solver.

Stop it with:

```bash
docker compose --profile game down
```

`docker compose down -v` deletes the database volume.

---

## AWS layout

```
Internet
   |  TCP 55550, TCP 22 (your IP only)
   v
EC2 t3.small, Elastic IP        public subnet, AL2023, Docker + Compose
   |  TCP 5432 (security-group to security-group)
   v
RDS PostgreSQL 17               private subnets, no public access
```

Terraform creates a dedicated VPC (two public and two private subnets, no NAT
gateway), ECR, the EC2 host, Elastic IP, RDS, SSM parameters, CloudWatch Logs,
and the IAM role used to pull the image and read the runtime URL.

The database schema and the restricted `runtime` role are created by
`tools/init_db.py` during the host bootstrap. It is safe to re-run.

### Costs

Roughly **USD 40/month** in `ap-southeast-1` while running:

| Item | Approx. |
|---|---|
| `t3.small` on-demand | $18 |
| Public IPv4 address | $4 |
| 20 GiB gp3 root volume | $2 |
| `db.t4g.micro` + 20 GiB | $18 |
| ECR, CloudWatch, backups | < $2 |

`make destroy` removes all of it. `db_skip_final_snapshot = true` means the
database is deleted without a snapshot — do not point this at real records.

---

## Prerequisites

- Terraform 1.6+
- AWS CLI v2 with a working profile (the toolkit uses `agent-toolkit`)
- Docker with Buildx
- An SSH key at `~/.ssh/id_ed25519.pub` (override with `ssh_public_key_path`)

Confirm the project Region in **AWS Settings → View all projects → Overview →
Additional Info → Region**. The new AWS experience assigns one Region and
resources cannot be created elsewhere. Keep `aws_region` in
`terraform.tfvars` aligned with it.

If `aws` and `terraform` are not on your PATH yet, the toolkit setup has not
finished; the Docker image and Terraform source can still be built and
validated locally.

---

## Configure

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
aws_region  = "ap-southeast-1"
aws_profile = "agent-toolkit"
ssh_cidrs   = ["203.0.113.10/32"]   # curl -s https://checkip.amazonaws.com
```

`terraform.tfvars` and all `.tfstate` files are git-ignored. Local state contains
the generated runtime password; use an encrypted remote backend if the state is
ever shared.

If Terraform cannot see the toolkit's login session, export credentials for
the shell first:

```bash
eval "$(aws configure export-credentials --profile agent-toolkit --format env)"
```

---

## First deploy

The image must exist before the host boots, so ECR is created separately:

```bash
cd infra/terraform
make init
make ecr          # create the ECR repository
make push         # build linux/amd64, push :<git-sha>
make apply        # create VPC, RDS, EC2, parameters
make bootstrap    # re-run bootstrap once the stack is up
```

`make apply` takes about 10–15 minutes, mostly RDS.

`make ship` runs all four in order for later releases.

Check the result:

```bash
terraform output
curl "$(terraform output -raw browser_check)"
```

Then, on a player's computer:

```bash
python client.py <elastic-ip>
```

LAN auto-discovery is disabled in AWS (broadcast does not cross the internet),
so the address is required.

---

## Operations

| Task | Command |
|---|---|
| Tail container logs | `make logs` |
| Shell on the host | `make ssh` |
| Re-run bootstrap | `make bootstrap` |
| Re-run schema/role only | `make db-init` |
| Release a new image | `make ship` |
| Show outputs | `make output` |
| Tear everything down | `make destroy` |

The host bootstrap log is `/var/log/find-my-mines-bootstrap.log`. Runtime
configuration is `/opt/find-my-mines/.env` and `.env.init`, both mode `0600`.
The app container logs to CloudWatch Logs group `/find-my-mines/dev/game`.

---

## What is intentionally not here

- **More than one game host.** Rooms, boards, chat, and reconnect tokens live
  in server memory. Running two instances would split players across two
  worlds. `desired_count`-style scaling is not part of this scaffold.
- **Zero-downtime deploys.** Restarting the container drops in-progress rooms.
  Completed matches already committed to RDS survive.
- **TLS or a domain.** The game speaks raw TCP. Adding TLS would require
  changing `protocol.py` and `client.py`.
- **Public database access.** RDS is reachable only from the game host.
  Use `make db-init` / `make ssh` for maintenance rather than opening 5432.
- **Multi-server cache sharing.** `stats.DatabaseLeaderboard` assumes one
  server process.

---

## Troubleshooting

**`make push` fails with "Unable to locate credentials".**
The new login session is not visible to the AWS CLI. Run
`aws sts get-caller-identity --profile agent-toolkit`, then export credentials
as shown above. Terraform uses the same profile variable.

**`terraform apply` fails with a Region/permission error.**
Check `aws_region` against AWS Settings. Resources cannot be created in a
Region other than the project's assigned Region.

**The container is not running after the first `make apply`.**
The ECR repository was empty when the host booted. Push the image and re-run
`make bootstrap`. Check `/var/log/find-my-mines-bootstrap.log` over `make ssh`.

**`make bootstrap` says database init failed.**
RDS is still creating or the security group is not ready. Wait a minute and
run `make bootstrap` again; the script retries five times on its own.

**SSH times out.**
`ssh_cidrs` does not contain your current public IP, or the key does not match
`ssh_public_key_path`. Update the variable and `make apply`.

**`make destroy` fails on the database.**
Set `db_deletion_protection = false` (the default) and retry. If
`db_skip_final_snapshot` is false, RDS requires a final snapshot name, which
Terraform derives automatically.

**Logs are empty.**
The container may not have started, or the instance role lacks
`logs:PutLogEvents` on the group. Check `make ssh` and
`sudo docker compose -f compose.aws.yaml ps`.
