# Base image for the sandbox environment where the agent will run tests
FROM python:3.11-slim

# Install system dependencies commonly needed for standard projects
RUN apt-get update && apt-get install -y \
    ca-certificates \
    git \
    curl \
    gnupg \
    build-essential \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install Node 22 so sandbox validation matches the repository runtime policy.
RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y nodejs \
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
