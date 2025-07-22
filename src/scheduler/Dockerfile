# Use an official Python runtime as a parent image
FROM python:3.11-slim-bookworm

# Update system packages to reduce vulnerabilities
RUN apt-get update && apt-get upgrade -y && apt-get clean && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file to the working directory
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the scheduler source code (e.g., scheduler.py)
COPY .. .

# Expose the port your webhook server will listen on for HTTPS traffic
EXPOSE 8443

# Run scheduler.py when the container launches
# Your script must start an HTTPS server on port 8443 using certs mounted at /etc/webhook/certs
CMD ["python", "scheduler/scheduler.py"]