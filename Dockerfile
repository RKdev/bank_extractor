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

# Copy all parser scripts and the controller script from build directory
COPY build/bank_extractor.py .
COPY build/bank_parser_2007.py .
COPY build/bank_parser_2009.py .
COPY build/bank_parser_2015_plus.py .

# Create necessary directories that match the docker-compose volume mapping
RUN mkdir -p /app/data/input /app/data/output

# Create format subdirectories in input directory
RUN mkdir -p /app/data/input/2007_format /app/data/input/2009_format /app/data/input/2015_format

# Set default environment variables for directories
ENV INPUT_DIR=/app/data/input
ENV OUTPUT_DIR=/app/data/output

# Make scripts executable
RUN chmod +x bank_extractor.py
RUN chmod +x bank_parser_2007.py
RUN chmod +x bank_parser_2009.py
RUN chmod +x bank_parser_2015_plus.py

# Set the entry point to use the controller script
ENTRYPOINT ["python", "bank_extractor.py"]

# Default command arguments - do not use ${VAR} syntax in CMD as it won't be expanded
CMD ["--input-dir", "/app/data/input", "--output-dir", "/app/data/output", "--format", "tsv"]
