FROM python:3.9-slim

WORKDIR /app

# Install system dependencies for pdfplumber
RUN apt-get update && apt-get install -y \
    build-essential \
    libpoppler-cpp-dev \
    pkg-config \
    python3-dev \
    tree \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy the updated requirements file from build directory
COPY build/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code from build directory
COPY build/bank_extractor.py .

# Create necessary directories that match the docker-compose volume mapping
RUN mkdir -p /app/data/input /app/data/output

# Set default environment variables for directories
ENV INPUT_DIR=/app/data/input
ENV OUTPUT_DIR=/app/data/output

# Make script executable
RUN chmod +x bank_extractor.py

# Set the entry point to use shell form to ensure environment variable expansion
ENTRYPOINT ["python", "bank_extractor.py"]
# Default command arguments - do not use ${VAR} syntax in CMD as it won't be expanded
CMD ["--input-dir", "/app/data/input", "--output-dir", "/app/data/output", "--format", "tsv"]
