# Source Code (`/src`)

This directory contains all the core source code for the project.

- **`/scaler`**: Implements the autoscaling logic for the number of function instances.
- **`/scheduler`**: Contains the custom scheduler implementation (`scheduler.py`). This is the heart of the system, responsible for deciding the placement of function pods based on resource conditions and model characteristics, and for handling shared storage mounts. 
- **`/shuffler`**: Implements the logic for dynamically shuffling models between CPU and GPU, allowing inactive models to be offloaded from the GPU when resources are scarce.
- **`/utils`**: Contains common utility functions used by other modules.