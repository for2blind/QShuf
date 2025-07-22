import subprocess
import time
from collections import defaultdict
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import requests as request
config.load_kube_config()

v1_core = client.CoreV1Api()
apps_v1 = client.AppsV1Api()



def update_pod_deletion_cost(pod_name, deletion_cost,namespace='openfaas-fn'):
    try:
        pod = v1_core.read_namespaced_pod(name=pod_name, namespace=namespace)

        if pod.metadata.annotations is None:
            pod.metadata.annotations = {}

        pod.metadata.annotations["controller.kubernetes.io/pod-deletion-cost"] = str(deletion_cost)

        v1_core.patch_namespaced_pod(name=pod_name, namespace=namespace, body=pod)
        print(f"Updated pod-deletion-cost for {pod_name} to {deletion_cost}")

    except ApiException as e:
        print(f"Exception when updating pod-deletion-cost: {e}")

def find_replicaset_from_pod(pod_name,namespace='openfaas-fn'):
    try:
        pod = v1_core.read_namespaced_pod(name=pod_name, namespace=namespace)

        for owner in pod.metadata.owner_references:
            if owner.kind == "ReplicaSet":
                return owner.name
        print(f"No ReplicaSet found for pod {pod_name}")
        return None

    except ApiException as e:
        print(f"Exception when finding ReplicaSet from pod: {e}")
        return None

def find_deployment_from_replicaset(replicaset_name,namespace='openfaas-fn'):
    try:
        replicaset = apps_v1.read_namespaced_replica_set(name=replicaset_name, namespace=namespace)

        for owner in replicaset.metadata.owner_references:
            if owner.kind == "Deployment":
                return owner.name
        print(f"No Deployment found for ReplicaSet {replicaset_name}")
        return None

    except ApiException as e:
        print(f"Exception when finding Deployment from ReplicaSet: {e}")
        return None

def scale_deployment(deployment_name, num_replicas,namespace='openfaas-fn'):
    try:
        cmd = ["kubectl", "scale", "deployment", deployment_name, "--replicas", str(num_replicas), "--namespace", namespace]
        subprocess.run(cmd, check=True)
        print(f"Scaled deployment {deployment_name} to {num_replicas} replicas.")

    except subprocess.CalledProcessError as e:
        print(f"Error scaling deployment {deployment_name}: {e}")

def request_pod_api(pod_name, getapi,namespace='openfaas-fn'):
    try:
        if getapi == 'cpu':
            result = v1_core.connect_get_namespaced_pod_proxy_with_path(f"{pod_name}:5001", namespace, "tocpu")
        elif getapi == 'cuda':
            result = v1_core.connect_get_namespaced_pod_proxy_with_path(f"{pod_name}:5001", namespace, "tocuda")
        elif getapi == 'notready':
            result = v1_core.connect_get_namespaced_pod_proxy_with_path(f"{pod_name}:5001", namespace, "tonotready")
        elif getapi == 'ready':
            result = v1_core.connect_get_namespaced_pod_proxy_with_path(f"{pod_name}:5001", namespace, "toready")
        else:
            raise ValueError("getapi must be 'cpu' or 'cuda' or 'notready' or 'ready'")
        return result
    except Exception as e:
        print(f"Error requesting {getapi} from Pod {pod_name}: {e}")
        return None

def update_pod_label(pod_name, label_key='infer_device', label_value='cuda',namespace='openfaas-fn'):
    patch = {
        "metadata": {
            "labels": {
                label_key: label_value
            }
        }
    }
    try:
        v1_core.patch_namespaced_pod(name=pod_name, namespace=namespace, body=patch)
        print(f"Successfully updated {pod_name} with label {label_key}={label_value}")
    except ApiException as e:
        print(f"Error updating label for Pod {pod_name}: {e}")

def delete_pod_and_adjust_replica(pod_name,namespace='openfaas-fn'):
    replicaset_name = find_replicaset_from_pod(pod_name)
    if replicaset_name:
        deployment_name = find_deployment_from_replicaset(replicaset_name)
        if deployment_name:
            v1_core.delete_namespaced_pod(name=pod_name, namespace=namespace)
            print(f"Deleted pod {pod_name}")

            current_replicas = int(subprocess.check_output(
                ["kubectl", "get", "deployment", deployment_name, "-n", namespace, "-o", "jsonpath={.spec.replicas}"]
            ))
            scale_deployment(deployment_name, max(current_replicas - 1, 0))

def delete_cpu_cuda_pod(namespace="openfaas-fn",operate="cpu",target_pods=[]):
    # namespace = "openfaas-fn"
    for pod_name in target_pods:
        if operate == 'delete':        
            update_pod_deletion_cost(pod_name, deletion_cost=-100)
            delete_pod_and_adjust_replica(pod_name)
        elif operate in ['cpu', 'cuda']:
            print(f"{pod_name} to {operate} ...")
            result = request_pod_api(pod_name, operate)
            if result is not None:
                print(f"{operate.upper()} info from {pod_name}: {result}")
                update_pod_label(pod_name, label_key='infer_device', label_value=operate)
        elif operate in ['notready','ready']:
            result = request_pod_api(pod_name, operate)            
            print(f"{operate.upper()} info from {pod_name}: {result}")
        else:
            print("Invalid operation. Please choose from 'delete', 'cpu', 'cuda'.")
            
def list_deployments_and_pods(namespace="openfaas-fn"):
    deployment_pods_dict = {}
    try:
        deployments = apps_v1.list_namespaced_deployment(namespace)
        for deployment in deployments.items:
            deployment_name = deployment.metadata.name
            deployment_pods_dict[deployment_name] = []
            pods = v1_core.list_namespaced_pod(namespace, label_selector=f"faas_function={deployment_name}")
            for pod in pods.items:
                infer_device = pod.metadata.labels.get('infer_device', 'N/A')
                deployment_pods_dict[deployment_name].append({
                    'pod_name': pod.metadata.name,
                    'infer_device': infer_device
                })
    
    except ApiException as e:
        print(f"Exception when listing deployments and pods: {e}")
    
    return deployment_pods_dict

def get_pods_for_deployment(deployment_name, namespace='openfaas-fn'):
    pods_info = []
    try:
        pods = v1_core.list_namespaced_pod(namespace, label_selector=f"app={deployment_name}")
        for pod in pods.items:
            infer_device = pod.metadata.labels.get('infer_device', 'N/A')
            pods_info.append({
                'pod_name': pod.metadata.name,
                'infer_device': infer_device
            })    
    except ApiException as e:
        print(f"Exception when getting pods for deployment {deployment_name}: {e}")
    
    return pods_info


if __name__ == "__main__":
    deployment_pods = list_deployments_and_pods()
    for deployment, pods in deployment_pods.items():
        print(f"Deployment: {deployment}")
        for pod in pods:
            print(f"  Pod: {pod['pod_name']}, infer_device: {pod['infer_device']}")
    

