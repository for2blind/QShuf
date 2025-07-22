import subprocess
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import time
from collections import defaultdict

config.load_kube_config()

v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api() 
namespace = "openfaas-fn"
target_pods = []

def update_pod_deletion_cost(pod_name, namespace, deletion_cost):
    try:
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)

        if pod.metadata.annotations is None:
            pod.metadata.annotations = {}

        pod.metadata.annotations["controller.kubernetes.io/pod-deletion-cost"] = str(deletion_cost)

        v1.patch_namespaced_pod(name=pod_name, namespace=namespace, body=pod)
        print(f"Updated pod-deletion-cost for {pod_name} to {deletion_cost}")

    except ApiException as e:
        print(f"Exception when updating pod-deletion-cost: {e}")

def find_replicaset_from_pod(pod_name, namespace):
    try:
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        
        for owner in pod.metadata.owner_references:
            if owner.kind == "ReplicaSet":
                return owner.name
        print(f"No ReplicaSet found for pod {pod_name}")
        return None

    except ApiException as e:
        print(f"Exception when finding ReplicaSet from pod: {e}")
        return None

def find_deployment_from_replicaset(replicaset_name, namespace):
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

def scale_deployment(deployment_name, namespace, num_replicas):
    try:
        cmd = ["kubectl", "scale", "deployment", deployment_name, "--replicas", str(num_replicas), "--namespace", namespace]
        
        subprocess.run(cmd, check=True)
        print(f"Scaled deployment {deployment_name} to {num_replicas} replicas.")
    
    except subprocess.CalledProcessError as e:
        print(f"Error scaling deployment {deployment_name}: {e}")

if __name__ == "__main__":
    deletion_cost = -100
    for pod_name in target_pods:
        update_pod_deletion_cost(pod_name=pod_name, namespace=namespace, deletion_cost=deletion_cost)

    time.sleep(0.5)

    deployment_pod_count = defaultdict(int)

    for pod_name in target_pods:
        replicaset_name = find_replicaset_from_pod(pod_name=pod_name, namespace=namespace)
        if replicaset_name:
            deployment_name = find_deployment_from_replicaset(replicaset_name=replicaset_name, namespace=namespace)
            if deployment_name:
                deployment_pod_count[deployment_name] += 1

    for deployment_name, pod_count in deployment_pod_count.items():
        current_replicas = int(subprocess.check_output(
            ["kubectl", "get", "deployment", deployment_name, "-n", namespace, "-o", "jsonpath={.spec.replicas}"]
        ))
        new_replicas = max(current_replicas - pod_count, 0)
        scale_deployment(deployment_name, namespace, new_replicas)
