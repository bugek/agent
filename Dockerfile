# Base image for the sandbox environment where the agent will run tests
FROM python:3.11-slim

# Install system dependencies commonly needed for standard projects
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    nodejs \
    npm \
    jq \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g pnpm yarn

# Set up a generic non-root user for running code safely
RUN useradd -m sandboxuser
WORKDIR /home/sandboxuser/workspace

# Copy testing scripts or common utilities if needed
# COPY tests/ /home/sandboxuser/tests/

USER sandboxuser

# Default command keeps the container alive so we can exec into it
CMD ["tail", "-f", "/dev/null"]
