FROM python:3.11-slim

WORKDIR /app

# System dependencies for web3/crypto compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Port untuk Flask dashboard
EXPOSE 6061

# Jalankan bot + dashboard
CMD ["python", "run.py"]
