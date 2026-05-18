#!/bin/bash
# =============================================================================
# PAGIBOTS — Google Cloud Deployment Script
# =============================================================================
# Jalankan script ini dari komputer lokal untuk deploy ke GCE.
#
# Prasyarat:
#   1. Install gcloud CLI: https://cloud.google.com/sdk/docs/install
#   2. Login: gcloud auth login
#   3. Sudah buat project: gcloud config set project <PROJECT_ID>
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh setup    # Pertama kali: buat VM + firewall
#   ./deploy.sh deploy   # Upload & jalankan bot
#   ./deploy.sh logs     # Lihat logs
#   ./deploy.sh ssh      # SSH ke VM
#   ./deploy.sh backup   # Download database backup
#   ./deploy.sh update   # Update kode + restart bot
# =============================================================================

set -e

# ── Configuration ──
VM_NAME="pagibots-vm"
ZONE="us-central1-a"
MACHINE_TYPE="e2-micro"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

print_header() {
    echo -e "\n${CYAN}╔══════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║  🤖 PAGIBOTS — GCP Deployment Tool       ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}\n"
}

# ── SETUP: First-time VM creation ──
cmd_setup() {
    echo -e "${YELLOW}[1/3] Creating VM instance (Always Free Tier)...${NC}"
    gcloud compute instances create $VM_NAME \
        --zone=$ZONE \
        --machine-type=$MACHINE_TYPE \
        --image-family=cos-stable \
        --image-project=cos-cloud \
        --boot-disk-size=30GB \
        --boot-disk-type=pd-standard \
        --tags=http-server \
        --metadata=google-logging-enabled=true \
        || echo -e "${YELLOW}VM may already exist, continuing...${NC}"

    echo -e "${YELLOW}[2/3] Creating firewall rule for dashboard (port 6061)...${NC}"
    gcloud compute firewall-rules create allow-pagibots-dashboard \
        --direction=INGRESS \
        --priority=1000 \
        --network=default \
        --action=ALLOW \
        --rules=tcp:6061 \
        --target-tags=http-server \
        --source-ranges=0.0.0.0/0 \
        || echo -e "${YELLOW}Firewall rule may already exist, continuing...${NC}"

    echo -e "${YELLOW}[3/3] Getting VM external IP...${NC}"
    EXTERNAL_IP=$(gcloud compute instances describe $VM_NAME \
        --zone=$ZONE \
        --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

    echo -e "\n${GREEN}✅ Setup complete!${NC}"
    echo -e "   VM External IP: ${CYAN}${EXTERNAL_IP}${NC}"
    echo -e "   Dashboard URL:  ${CYAN}http://${EXTERNAL_IP}:6061${NC}"
    echo -e "\n   Next step: ${YELLOW}./deploy.sh deploy${NC}"
}

# ── DEPLOY: Upload files & start container ──
cmd_deploy() {
    echo -e "${YELLOW}[1/4] Uploading project files to VM...${NC}"

    # Create a temp tar excluding unnecessary files
    tar czf /tmp/pagibots-deploy.tar.gz \
        --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='.cursor' \
        --exclude='.vscode' \
        --exclude='debug-*.log' \
        --exclude='polybots.db' \
        --exclude='_*.py' \
        --exclude='calc_*.py' \
        --exclude='test_*.py' \
        --exclude='check_*.py' \
        --exclude='diagnose_*.py' \
        --exclude='scratch.py' \
        -C "$PROJECT_DIR" .

    gcloud compute scp /tmp/pagibots-deploy.tar.gz \
        $VM_NAME:~/pagibots-deploy.tar.gz --zone=$ZONE

    echo -e "${YELLOW}[2/4] Extracting files on VM...${NC}"
    gcloud compute ssh $VM_NAME --zone=$ZONE --command="
        mkdir -p ~/PAGIBOTS && \
        cd ~/PAGIBOTS && \
        tar xzf ~/pagibots-deploy.tar.gz && \
        rm ~/pagibots-deploy.tar.gz
    "

    echo -e "${YELLOW}[3/4] Building Docker image on VM...${NC}"
    gcloud compute ssh $VM_NAME --zone=$ZONE --command="
        cd ~/PAGIBOTS && \
        docker build -t pagibots:latest .
    "

    echo -e "${YELLOW}[4/4] Starting container...${NC}"
    gcloud compute ssh $VM_NAME --zone=$ZONE --command="
        docker stop pagibots 2>/dev/null || true && \
        docker rm pagibots 2>/dev/null || true && \
        cd ~/PAGIBOTS && \
        docker run -d \
            --name pagibots \
            --restart unless-stopped \
            -p 6061:6061 \
            -v ~/PAGIBOTS/polybots.db:/app/polybots.db \
            --env-file .env \
            pagibots:latest
    "

    EXTERNAL_IP=$(gcloud compute instances describe $VM_NAME \
        --zone=$ZONE \
        --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

    echo -e "\n${GREEN}✅ Deployment complete!${NC}"
    echo -e "   Dashboard: ${CYAN}http://${EXTERNAL_IP}:6061${NC}"
    echo -e "   Logs:      ${YELLOW}./deploy.sh logs${NC}"
}

# ── UPDATE: Pull changes & restart ──
cmd_update() {
    echo -e "${YELLOW}Updating bot...${NC}"
    cmd_deploy
}

# ── LOGS: View container logs ──
cmd_logs() {
    echo -e "${CYAN}📋 Showing bot logs (Ctrl+C to exit)...${NC}\n"
    gcloud compute ssh $VM_NAME --zone=$ZONE --command="docker logs --tail 100 -f pagibots"
}

# ── SSH: Connect to VM ──
cmd_ssh() {
    echo -e "${CYAN}🔌 Connecting to VM...${NC}"
    gcloud compute ssh $VM_NAME --zone=$ZONE
}

# ── BACKUP: Download database ──
cmd_backup() {
    BACKUP_FILE="polybots_backup_$(date +%Y%m%d_%H%M%S).db"
    echo -e "${YELLOW}📦 Downloading database backup...${NC}"
    gcloud compute scp $VM_NAME:~/PAGIBOTS/polybots.db "./$BACKUP_FILE" --zone=$ZONE
    echo -e "${GREEN}✅ Backup saved: ${BACKUP_FILE}${NC}"
}

# ── STATUS: Check VM and container status ──
cmd_status() {
    echo -e "${CYAN}🔍 Checking status...${NC}\n"

    echo -e "${YELLOW}VM Status:${NC}"
    gcloud compute instances describe $VM_NAME --zone=$ZONE \
        --format='table(name, status, networkInterfaces[0].accessConfigs[0].natIP)'

    echo -e "\n${YELLOW}Container Status:${NC}"
    gcloud compute ssh $VM_NAME --zone=$ZONE --command="docker ps -a --filter name=pagibots"
}

# ── STOP: Stop the bot container ──
cmd_stop() {
    echo -e "${YELLOW}⏹️  Stopping bot...${NC}"
    gcloud compute ssh $VM_NAME --zone=$ZONE --command="docker stop pagibots"
    echo -e "${GREEN}✅ Bot stopped${NC}"
}

# ── START: Start the bot container ──
cmd_start() {
    echo -e "${YELLOW}▶️  Starting bot...${NC}"
    gcloud compute ssh $VM_NAME --zone=$ZONE --command="docker start pagibots"
    echo -e "${GREEN}✅ Bot started${NC}"
}

# ── Main ──
print_header

case "${1:-help}" in
    setup)   cmd_setup ;;
    deploy)  cmd_deploy ;;
    update)  cmd_update ;;
    logs)    cmd_logs ;;
    ssh)     cmd_ssh ;;
    backup)  cmd_backup ;;
    status)  cmd_status ;;
    stop)    cmd_stop ;;
    start)   cmd_start ;;
    *)
        echo "Usage: ./deploy.sh <command>"
        echo ""
        echo "Commands:"
        echo -e "  ${GREEN}setup${NC}    First-time: create VM + firewall (run once)"
        echo -e "  ${GREEN}deploy${NC}   Upload code & start bot container"
        echo -e "  ${GREEN}update${NC}   Update code & restart (same as deploy)"
        echo -e "  ${GREEN}logs${NC}     View bot logs in real-time"
        echo -e "  ${GREEN}ssh${NC}      SSH into the VM"
        echo -e "  ${GREEN}backup${NC}   Download database backup"
        echo -e "  ${GREEN}status${NC}   Check VM & container status"
        echo -e "  ${GREEN}stop${NC}     Stop the bot container"
        echo -e "  ${GREEN}start${NC}    Start the bot container"
        echo ""
        ;;
esac
