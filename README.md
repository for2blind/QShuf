# QShuf - Artifact Evaluation Guide

This document serves as the artifact evaluation guide for the paper titled "[Your Paper Title]". It provides detailed instructions on how to set up the environment, run the experiments, and reproduce the results presented in the paper.

The core of this project is a custom scheduling system built on Kubernetes for optimizing deep learning inference tasks on the OpenFaaS serverless framework. The system aims to improve GPU utilization and reduce latency through strategies like dynamic scheduling, resource scaling, and model shuffling (offloading/loading).

## Directory Structure

To facilitate understanding and usage, the project is organized into the following structure. Each main directory contains its own `README.md` file explaining its purpose.

* **`/` (Root Directory)**
    * `README.md`: (This file) The main guide for evaluating this artifact.
* **`/src`**
    * `README.md`: Explains the core source code, including the custom scheduler, scaler, and model shuffling logic.
* **`/workloads`**
    * `README.md`: Contains all AI models, OpenFaaS function definitions, and related configurations used in the experiments.
* **`/scripts`**
    * `README.md`: Contains all scripts for setting up the environment, running experiments, collecting data, and processing results.
* **`/results`**
    * `README.md`: Explains the contents of the output directory, which holds logs, raw data, and summarized results from experiments.

---

## 1. Prerequisites

Before you begin, please ensure you have the following environment set up. The requirements for this project are significant, so please check them carefully.

* **Hardware**:
    * A multi-node Kubernetes cluster with at least one node equipped with an NVIDIA GPU.
    * A server for shared storage (e.g., a separate VM or physical machine).
* **Software**:
    * **Kubernetes (k8s)**: Version >= 1.22.
    * **Docker**: For building function images.
    * **OpenFaaS**: Deployed on the Kubernetes cluster.
    * **NFS (Network File System)**: Or another shared storage solution, with `nfs-subdir-external-provisioner` configured for dynamic Persistent Volume creation in k8s.
    * **Harbor**: Or another Docker image registry accessible by the Kubernetes cluster.
    * **Prometheus**: Deployed in the cluster for metrics monitoring.
    * **NVIDIA GPU Operator**: Or a manual installation of `dcgm-exporter` to expose GPU metrics to Prometheus.
    * **Python**: >= 3.8, for running scripts.
    * **faas-cli**: The OpenFaaS command-line tool.

---

## 2. Environment Setup (Step-by-Step)

Please follow these steps strictly to configure the environment.

### Step 2.1: Kubernetes & OpenFaaS Foundation

1.  **Prepare K8s Cluster**: Ensure your `kubectl` is configured and has access to your cluster.
2.  **Deploy OpenFaaS**: If you haven't deployed OpenFaaS yet, please follow the [official documentation](https://docs.openfaas.com/deployment/kubernetes/) for deployment.
3.  **Create Function Namespace**: Our system runs functions in a specific namespace. Create and label it as follows:
    ```bash
    # Create the namespace
    kubectl create namespace openfaas-fn

    # Add a label to the namespace so the openfaas operator can recognize it
    kubectl label namespace openfaas-fn [openfaas.com/function-namespace=true](https://openfaas.com/function-namespace=true)
    ```

### Step 2.2: Shared Storage & Model Preparation

1.  **Set up NFS Server**: On your shared storage server, create a directory to store the models. For example: `/sharing/models`.
2.  **Download Models**: Download all model files required for the experiments (e.g., `.pt`, `.bin` files) and place them in the shared directory on the NFS server.
3.  **Modify Model Paths in Code**: This is a **critical step**. You must manually edit the path in each model's code to point to the correct location within the shared storage.
    * **File Location**: `workloads/models/[model_name]/index.py`
    * **What to change**: In each `index.py` file, find the line that loads the model and ensure the path points to the mounted shared storage directory inside the container. For example, if the shared storage is mounted at `/models` inside the container, the code should use `/models/[model_file_name]`.

### Step 2.3: Deploy Prometheus & GPU Monitoring

1.  **Deploy Prometheus**: Deploy Prometheus in your cluster using Helm or another method.
2.  **Deploy DCGM Exporter**: To collect GPU metrics (like utilization, memory usage), you need to deploy `dcgm-exporter`. Using the [NVIDIA GPU Operator](https://docs.nvidia.com/gpu-operator/latest/index.html) is highly recommended as it automatically installs all necessary drivers and monitoring components.

### Step 2.4: Configure, Build, & Push Function Images

This process is managed centrally using the OpenFaaS configuration file (`config.yml`), which simplifies building and pushing images for all functions at once.

1.  **Configure the YAML file**: Open the main configuration file located at `workloads/configs/config.yml`. You **must edit the `image` tag** for every function defined in this file to point to your container registry.

    For example, find the `mobilenet` function definition and change its `image` value:
    ```yaml
    # In workloads/configs/config.yml

    # Before editing:
    functions:
      mobilenet:
        lang: python3-http-dl
        handler: ./models/mobilenet
        image: harbor.example/model:1.0 # <-- EDIT THIS LINE
        # ... other settings

    # After editing (example):
    functions:
      mobilenet:
        lang: python3-http-dl
        handler: ./models/mobilenet
        image: [your-harbor-ip]/[your-project]/mobilenet:1.0 # <-- UPDATED
        # ... other settings
    ```
    **Important**: Repeat this for all functions listed in `config.yml` (e.g., `bert`, `resnet-50`, etc.). Also, ensure the `provider.gateway` URL at the top of the file points to your OpenFaaS gateway.

2.  **Login to your Registry**: Make sure you are logged into your container registry via the Docker CLI.
    ```bash
    docker login [your-harbor-ip]
    ```

3.  **Build and Push with `faas-cli`**: Use the `faas-cli` tool with the modified YAML file to build and push all function images. Run these commands from the **project's root directory**.
    ```bash
    # Build all function images defined in the config file
    faas-cli build -f workloads/configs/config.yml

    # Push all the newly built images to your registry
    faas-cli push -f workloads/configs/config.yml
    ```
    This automated process replaces the need to manually build and push each Docker image individually.

### Step 2.5: Deploying the Custom Scheduler & Webhook

This is the most critical part of the setup. We will deploy our Python scheduler as a service inside the cluster and then create a `ValidatingAdmissionWebhook` to direct pod creation requests from the `openfaas-fn` namespace to our scheduler.
1.  **Build and Push the Scheduler Image**Now, build the Docker image and push it to your registry.
    ```bash
    # Navigate to the scheduler source code directory
    cd src/scheduler
    
    # Build the scheduler image
    docker build -t [your-harbor-ip]/[your-project]/custom-scheduler:latest .

    # Push the image to your registry
    docker push [your-harbor-ip]/[your-project]/custom-scheduler:latest

    ```

2.  **Generate TLS Certificates**The Kubernetes API server requires that webhooks be served over HTTPS. We will generate a self-signed certificate for this purpose.

    ```bash
    # generate the CA key and certificate
    bash ./hook/gen_webhook.sh
    ```
    You should now have ca.crt, tls.key, and tls.crt files.

3.  **Create Kubernetes Secret for TLS** Store the server certificate and key in a Kubernetes secret so they can be mounted into our scheduler's pod.
    ```bash
    # Create a secret in the openfaas-fn namespace
    kubectl create secret tls custom-scheduler-tls \
        --cert=tls.crt \
        --key=tls.key \
        --namespace=openfaas-fn
    ```
4.  **Deploy the Scheduler (Deployment, Service, and RBAC)** This manifest creates the necessary permissions (ClusterRole, ClusterRoleBinding), the Deployment to run the scheduler script, and the Service to expose it internally.See scheduler-deployment.yaml and apply it.
    ```bash
    # Apply the deployment and service
    kubectl apply -f ./hook/scheduler-deployment.yaml
    ```
    Now, use the ca.crt file generated earlier to patch the caBundle into this configuration, then apply it.
    ```bash
    # Base64 encode the CA cert and patch it into the webhook config
    CA_BUNDLE=$(cat ca.crt | base64 | tr -d '\n')
    sed "s/caBundle: \"\"/caBundle: ${CA_BUNDLE}/" webhook-config.yaml > webhook-config-final.yaml

    # Apply the final configuration
    kubectl apply -f ./hook/webhook-config-final.yaml

    cd ../.. # Go back to the root directory
    ```
    **Warning**: This is the core of the entire system. If the webhook fails to intercept requests correctly, the system will fall back to using the default OpenFaaS scheduler.


---

## 3. Running the Experiments

After completing the setup, you can start running the experiments.

1.  **Deploy the Functions**: Use the `faas-cli` tool with the same configuration file to deploy all functions. The deployment requests will be intercepted by our webhook and processed by the custom scheduler.
    ```bash
    # From the project's root directory:
    faas-cli deploy -f workloads/configs/config.yml
    ```
    The scheduler will mount the necessary code and shared storage volumes to each function container according to its policy.

2.  **Run the Test Workload**: Use the evaluation scripts in `scripts/experiments` to send requests to the deployed functions, simulating a real-world workload.
    ```bash
    # Example command
    python3 scripts/experiments/auto_evaluation_wula_mu_ES1.py
    ```

---

## 4. Collecting & Analyzing Results

1.  **Execute the Data Collection Scripts**: After the experiment run, execute the scripts in `scripts/data_processing`. These scripts query GPU metrics from Prometheus and combine them with function invocation logs to generate the final performance data.
    ```bash
    python3 scripts/data_processing/collect_metrics_cv_wula.py
    ```

2.  **Review the Results**: All generated results will be saved in the `/results` directory.
    * `results/logs/`: Contains the raw log files.
    * `results/evaluation_record`: Contains detailed records for each evaluation run.
    * `results/summary.csv`: A summary table containing key performance indicators.
---

