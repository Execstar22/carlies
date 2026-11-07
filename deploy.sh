#!/bin/bash
# =============================================================================
# PAGIBOTS — Google Cloud Deployment Script
# =============================================================================
# Jalankan script ini dari komputer lokal untuk deploy ke GCE.
#
# Prasyarat:
#   1. Install gcloud CLI: https://cloud.google.com/sdk/docs/install
#   2. Login: gcloud auth login
#   3. Sudah set project: gcloud config set project <PROJECT_ID>
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh setup    # Pertama kali: buat VM + firewall
#   ./deploy.sh deploy   # Clone dari GitHub & jalankan bot
#   ./deploy.sh logs     # Lihat logs
#   ./deploy.sh ssh      # SSH ke VM
#   ./deploy.sh backup   # Download database backup
#   ./deploy.sh update   # Git pull & restart bot
# =============================================================================

set -e

# ── Configuration ──
VM_NAME="pagibots-vm"
ZONE="us-central1-a"
MACHINE_TYPE="e2-micro"
GITHUB_REPO="https://github.com/antarapi38-dev/tayamonibotakotaiamorobey.git"

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

# ── Get current public IP ──
get_my_ip() {
    curl -s https://ifconfig.me 2>/dev/null || curl -s https://api.ipify.org 2>/dev/null || echo ""
}

# ── SETUP: First-time VM creation ──
cmd_setup() {
    echo -e "${YELLOW}[1/4] Creating VM instance (Always Free Tier)...${NC}"
    gcloud compute instances create $VM_NAME \
        --zone=$ZONE \
        --machine-type=$MACHINE_TYPE \
        --image-family=cos-stable \
        --image-project=cos-cloud \
        --boot-disk-size=30GB \
        --boot-disk-type=pd-standard \
        --tags=pagibots-server \
        --metadata=google-logging-enabled=true \
        || echo -e "${YELLOW}VM may already exist, continuing...${NC}"

    echo -e "${YELLOW}[2/4] Detecting your public IP for firewall...${NC}"
    MY_IP=$(get_my_ip)
    if [ -z "$MY_IP" ]; then
        echo -e "${RED}Could not detect your IP. Enter manually:${NC}"
        read -p "Your public IP: " MY_IP
    fi
    echo -e "   Your IP: ${CYAN}${MY_IP}${NC}"

    echo -e "${YELLOW}[3/4] Creating firewall rule (RESTRICTED to your IP only)...${NC}"
    # Delete existing rule if present (to update IP)
    gcloud compute firewall-rules delete allow-pagibots-dashboard --quiet 2>/dev/null || true
    gcloud compute firewall-rules create allow-pagibots-dashboard \
        --direction=INGRESS \
        --priority=1000 \
        --network=default \
        --action=ALLOW \
        --rules=tcp:6061 \
        --target-tags=pagibots-server \
        --source-ranges="${MY_IP}/32" \
        || echo -e "${YELLOW}Firewall rule may already exist, continuing...${NC}"
    echo -e "   ${GREEN}✅ Only ${MY_IP} can access dashboard${NC}"

    echo -e "${YELLOW}[4/4] Getting VM external IP...${NC}"
    EXTERNAL_IP=$(gcloud compute instances describe $VM_NAME \
        --zone=$ZONE \
        --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

    echo -e "\n${GREEN}✅ Setup complete!${NC}"
    echo -e "   VM External IP: ${CYAN}${EXTERNAL_IP}${NC}"
    echo -e "   Dashboard URL:  ${CYAN}http://${EXTERNAL_IP}:6061${NC}"
    echo -e "   Access:         ${GREEN}Restricted to ${MY_IP} only${NC}"
    echo -e "\n   Next steps:"
    echo -e "   1. ${YELLOW}./deploy.sh deploy${NC}"
    echo -e "   2. SSH ke VM dan buat file .env: ${YELLOW}./deploy.sh ssh${NC}"
}

# ── DEPLOY: Clone from GitHub & start container ──
cmd_deploy() {
    echo -e "${YELLOW}[1/4] Cloning/pulling from GitHub on VM...${NC}"
    gcloud compute ssh $VM_NAME --zone=$ZONE --command="
        if [ -d ~/PAGIBOTS/.git ]; then
            echo 'Repository exists, pulling latest...'
            cd ~/PAGIBOTS && git pull
        else
            echo 'Cloning repository...'
            git clone ${GITHUB_REPO} ~/PAGIBOTS
        fi
    "

    echo -e "${YELLOW}[2/4] Checking .env file exists...${NC}"
    ENV_EXISTS=$(gcloud compute ssh $VM_NAME --zone=$ZONE --command="
        [ -f ~/PAGIBOTS/.env ] && echo 'yes' || echo 'no'
    " 2>/dev/null | tail -1)

    if [ "$ENV_EXISTS" = "no" ]; then
        echo -e "${RED}⚠️  File .env belum ada di VM!${NC}"
        echo -e "   Jalankan: ${YELLOW}./deploy.sh ssh${NC}"
        echo -e "   Lalu buat file .env di ~/PAGIBOTS/.env"
        echo -e "   Contoh isi .env:"
        echo -e "     TRADING_MODE=real"
        echo -e "     CLOUD_DEPLOY=true"
        echo -e "     POLY_PRIVATE_KEY=xxx"
        echo -e "     POLY_FUNDER_ADDRESS=xxx"
        echo -e "     POLY_PROXY_ADDRESS=xxx"
        echo -e "     POLY_SIGNATURE_TYPE=0"
        echo -e "     POLY_CHAIN_ID=137"
        echo -e "     REAL_BET_AMOUNT=1.10"
        echo -e "     MAX_BET_AMOUNT=3.00"
        echo -e "     MAX_DAILY_LOSS=3.30"
        echo -e "     POLYGON_RPC_URL=https://polygon-rpc.com"
        echo ""
        echo -e "   Setelah .env dibuat, jalankan lagi: ${YELLOW}./deploy.sh deploy${NC}"
        return 1
    fi

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

# ── UPDATE: Git pull & restart ──
cmd_update() {
    echo -e "${YELLOW}Updating bot from GitHub...${NC}"
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

# ── UPDATE-IP: Update firewall to new IP ──
cmd_update_ip() {
    echo -e "${YELLOW}Updating firewall to your current IP...${NC}"
    MY_IP=$(get_my_ip)
    if [ -z "$MY_IP" ]; then
        echo -e "${RED}Could not detect your IP. Enter manually:${NC}"
        read -p "Your public IP: " MY_IP
    fi

    gcloud compute firewall-rules update allow-pagibots-dashboard \
        --source-ranges="${MY_IP}/32"

    echo -e "${GREEN}✅ Firewall updated — access restricted to ${MY_IP}${NC}"
}

# ── Main ──
print_header

case "${1:-help}" in
    setup)      cmd_setup ;;
    deploy)     cmd_deploy ;;
    update)     cmd_update ;;
    logs)       cmd_logs ;;
    ssh)        cmd_ssh ;;
    backup)     cmd_backup ;;
    status)     cmd_status ;;
    stop)       cmd_stop ;;
    start)      cmd_start ;;
    update-ip)  cmd_update_ip ;;
    *)
        echo "Usage: ./deploy.sh <command>"
        echo ""
        echo "Commands:"
        echo -e "  ${GREEN}setup${NC}       First-time: create VM + firewall (run once)"
        echo -e "  ${GREEN}deploy${NC}      Clone from GitHub & start bot"
        echo -e "  ${GREEN}update${NC}      Git pull & restart (same as deploy)"
        echo -e "  ${GREEN}logs${NC}        View bot logs in real-time"
        echo -e "  ${GREEN}ssh${NC}         SSH into the VM"
        echo -e "  ${GREEN}backup${NC}      Download database backup"
        echo -e "  ${GREEN}status${NC}      Check VM & container status"
        echo -e "  ${GREEN}stop${NC}        Stop the bot container"
        echo -e "  ${GREEN}start${NC}       Start the bot container"
        echo -e "  ${GREEN}update-ip${NC}   Update firewall to your current IP"
        echo ""
        ;;
esac
