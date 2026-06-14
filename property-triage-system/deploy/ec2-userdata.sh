#!/bin/bash
# EC2 bootstrap (Amazon Linux 2023) — runs once at first boot as root.
# Installs Docker + Compose, clones the public repo, pulls the trained model
# from S3, and brings the four services up. AWS access is via the instance role.
set -xe
exec > /var/log/pt-deploy.log 2>&1

dnf install -y docker git
systemctl enable --now docker

# Docker Compose v2 plugin
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

cd /opt
git clone https://github.com/yuden404/ai-property-triage.git
cd ai-property-triage/property-triage-system

# Trained image model (git-ignored) from S3 → real classifier instead of the stub.
aws s3 cp s3://property-triage-listings-yrokach/deploy/model.pth \
  code/image_analyser/model.pth --region us-east-1 || echo "no model.pth — image runs as stub"

docker compose up --build -d
echo "PT_DEPLOY_DONE"
