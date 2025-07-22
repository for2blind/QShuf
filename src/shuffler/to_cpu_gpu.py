import requests
import subprocess
import time
from kubernetes import client, config

# Global API client to avoid repeated initialization
v1_core = None
v1_app = None

# def start_kubectl_proxy():
#     process = subprocess.Popen(["kubectl", "proxy"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
#     return process

# def wait_for_proxy():
    
def initialize_k8s_client():
    """
    Initialize the Kubernetes API client once and reuse it to avoid repeated setup.
    """
    global v1_core
    global v1_app
    if v1_core is None or v1_app is None:
        # Load the Kubernetes configuration only once
        config.load_kube_config()
        v1_core = client.CoreV1Api()
        v1_app = client.AppsV1Api()

# def get_pod_ip(pod_name, namespace='openfaas-fn'):
#     """
#     Get the IP address of a specific Pod.
#     """
#     # Ensure the Kubernetes client is initialized
#     initialize_k8s_client()

#     # Retrieve the list of Pods in the specified namespace
#     pods = v1_core.list_namespaced_pod(namespace)

#     # Find the matching Pod by name and return its IP address
#     for pod in pods.items:
#         if pod.metadata.name == pod_name:
#             return pod.status.pod_ip
    
#     raise ValueError(f"Pod {pod_name} not found in namespace {namespace}")

def request_pod_resource(pod_name, resource_type, namespace='openfaas-fn'):
    """
    Request the CPU or GPU resource path from the specified Pod.
    Args:
    - pod_name: Name of the Pod to request the resource from
    - resource_type: 'cpu' or 'cuda'
    - namespace: Kubernetes namespace (default is 'openfaas-fn')
    """
    # Get the Pod's IP address
    # pod_ip = get_pod_ip(pod_name, namespace)
    try:
        # Construct the URL path based on the requested resource type
        if resource_type == 'cpu':
            # url = f"http://{pod_ip}:5001/tocpu"
            # url = f"http://localhost:8001/api/v1/namespaces/{namespace}/pods/{pod_name}:5001/proxy/tocpu"
            result = v1_core.connect_get_namespaced_pod_proxy_with_path(f"{pod_name}:5001", namespace, "tocpu")
        elif resource_type == 'cuda':
            # url = f"http://{pod_ip}:5001/tocuda"
            # url = f"http://localhost:8001/api/v1/namespaces/{namespace}/pods/{pod_name}:5001/proxy/tocuda"
            result = v1_core.connect_get_namespaced_pod_proxy_with_path(f"{pod_name}:5001", namespace, "tocuda")
        else:
            raise ValueError("resource_type must be 'cpu' or 'cuda'")
        print("v1_core ",result)
        return result

    # Send the HTTP request to the Pod's 5001 port
    # try:
        # result = v1_core.connect_get_namespaced_pod_proxy_with_path(f"{pod_name}:5001", namespace, "tocuda")
        # print("v1_core ",result)
        # return result
        # print(f"Requesting get {resource_type} from {pod_name} with {url}")
        # response = requests.get(url)
        # response.raise_for_status()  # Raise an error for non-200 status codes
        # return response.json()  # Return the JSON response
    except requests.exceptions.RequestException as e:
        print(f"Error requesting {resource_type} from Pod {pod_name}: {e}")
        return None

def update_pod_label(pod_name, namespace='openfaas-fn', label_key='infer_device', label_value='cpu'):
    """
    Update the label of a specific Pod.
    Args:
    - pod_name: Name of the Pod to update
    - namespace: Kubernetes namespace
    - label_key: Label key to be set (default is 'infer_device')
    - label_value: Label value to be set (default is 'cpu')
    """
    # Ensure the Kubernetes client is initialized
    initialize_k8s_client()

    # Create a patch request to update the label
    patch = {
        "metadata": {
            "labels": {
                label_key: label_value
            }
        }
    }

    try:
        # Patch the Pod's metadata to update its labels
        v1_core.patch_namespaced_pod(name=pod_name, namespace=namespace, body=patch)
        print(f"Successfully updated {pod_name} with label {label_key}={label_value}")
    except client.exceptions.ApiException as e:
        print(f"Error updating label for Pod {pod_name}: {e}")
        
def delete_pod_and_adjust_replica(pod_name, namespace='openfaas-fn'):
    """
    Delete a Pod and adjust the replica count of its associated Deployment.
    Args:
    - pod_name: Name of the Pod to delete
    - namespace: Kubernetes namespace
    """
    # Ensure the Kubernetes client is initialized
    initialize_k8s_client()
    try:
        pod = v1_core.read_namespaced_pod(pod_name, namespace)
        
        owner_references = pod.metadata.owner_references
        deployment_name = None

        for owner in owner_references:
            if owner.kind == "ReplicaSet":
                replica_set = v1_app.read_namespaced_replica_set(owner.name, namespace)
                deployment_name = replica_set.metadata.labels.get("faas_function")
                break

        if deployment_name:
            deployment = v1_app.read_namespaced_deployment(deployment_name, namespace)

            
            new_replicas = deployment.spec.replicas - 1
            scale = client.V1Scale(
                spec=client.V1ScaleSpec(replicas=new_replicas)
            )
            # v1_core.delete_namespaced_pod(pod_name, namespace)
            # v1_core.delete_namespaced_pod(pod_name, namespace)
            print(f"Deleted pod: {pod_name}")
            # v1_core.patch_namespaced_replication_controller_scale(deployment_name, namespace, scale)
            print(f"Decreased replicas of deployment: {deployment_name} to {new_replicas}")
        else:
            print("Deployment not found for the given pod.")
        
    except client.exceptions.ApiException as e:
        print(f"Error deleting Pod {pod_name}: {e}")

# Example usage
if __name__ == "__main__":
    pod_name = ""  # Replace with your Pod name
    # resource_type = "delete"     # Can be 'cpu' or 'cuda' 'delete'
    resource_type = "cpu"
    if resource_type == 'delete':
        delete_pod_and_adjust_replica(pod_name, namespace='openfaas-fn')
    else:
        # Request the Pod's CPU/GPU resource path
        print(f"Requesting {resource_type} from {pod_name}...")
        result = request_pod_resource(pod_name, resource_type, namespace='openfaas-fn')
        
        if result is not None:
            print(f"{resource_type.upper()} info from {pod_name}: {result}")
            # Update the Pod's infer_device label based on the resource type
            update_pod_label(pod_name, namespace='openfaas-fn', label_key='infer_device', label_value=resource_type)
