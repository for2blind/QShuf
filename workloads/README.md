# Workloads (`/workloads`)

This directory defines all the AI models and services used in the experiments.

- **`/configs`**: Contains global configuration files, such as `model_size.csv`, `infer_time_theoretical.csv`, and the master `config.yml` for `faas-cli`.
- **`/models`**: Contains the function code for all AI models. Each subdirectory (e.g., `bert`, `resnet-50`) represents an individual OpenFaaS function. Inside each, `index.py` is the function handler entry point, and `requirements.txt` defines its Python dependencies. **Important:** Each `index.py` must be modified to point to the model file in the shared storage.
- **`/templates`**: Contains the Dockerfile templates used for building the OpenFaaS functions.