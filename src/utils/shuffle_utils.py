import subprocess
import time
from collections import defaultdict
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import requests as request
import pandas as pd
from kubernetes.client.exceptions import ApiException
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
            
import time
operate_pod_path_map = {
    'delete': 'delete',
    'cpu': 'tocpu',
    'cuda': 'tocuda',
    'notready': 'tonotready',
    'ready': 'toready'
}

def operate_pod_api(namespace="openfaas-fn", operate="cpu", target_pods=[]):
    """
    """
    for pod_name in target_pods:
        if operate == 'delete':
            update_pod_deletion_cost(pod_name, deletion_cost=-100)
            delete_pod_and_adjust_replica(pod_name)
            continue
        if operate not in operate_pod_path_map:
            print(f"Invalid operation {operate}. Please choose from 'delete', 'cpu', 'cuda', 'notready', 'ready'.")
            return
        path = operate_pod_path_map[operate]
        retries = 3
        result = None
        for attempt in range(retries):
            try:
                result = v1_core.connect_get_namespaced_pod_proxy_with_path(
                    f"{pod_name}:5001", namespace, path
                )
                if result:
                    break
            except Exception as e:
                print(f"[{pod_name}] Attempt {attempt+1} failed: {e}")
                time.sleep(1)

        if result:
            print(f"{operate.upper()} info from {pod_name}: {result}")
            if operate in ['cpu', 'cuda']:
                update_pod_label(pod_name, label_key='infer_device', label_value=operate)
            if operate in ['ready']:
                update_pod_label(pod_name, label_key='reserve', label_value=False)
        else:
            print(f"[{pod_name}] Failed to get valid response from {operate.upper()} API after {retries} attempts.")



def list_deployments_and_pods(namespace="openfaas-fn"):
    deployment_pods_list = []
    try:
        deployments = apps_v1.list_namespaced_deployment(namespace)
        if not deployments.items:
            print(f"No deployments found in namespace {namespace}")
            return None
        for deployment in deployments.items:
            deployment_name = deployment.metadata.name
            pods_df = get_pods_for_deployment(deployment_name, namespace)
            if pods_df is None or pods_df.empty:
                continue
            print(f"Deployment: {deployment_name}")
            pods_df['deployment_name'] = deployment_name
            deployment_pods_list.append(pods_df)    
    except ApiException as e:
        print(f"Exception when listing deployments and pods: {e}")
    if deployment_pods_list == []:
        return None
    return pd.concat(deployment_pods_list, ignore_index=True)


def get_pods_for_deployment(deployment_name, namespace='openfaas-fn'):
    pods_info_list = []
    try:
        pods = v1_core.list_namespaced_pod(namespace, label_selector=f"faas_function={deployment_name}")
        for pod in pods.items:
            infer_device = pod.metadata.labels.get('infer_device', 'N/A')
            reserve = pod.metadata.labels.get('reserve', 'False')
            pod_status = pod.status.phase
            is_ready = False
            crash_loop_backoff = False
            try:
                for container_status in pod.status.container_statuses:
                    if container_status.ready:
                        is_ready = True
                        # print(f"Pod {container_status}")
                    else:
                        if container_status.state.waiting and container_status.state.waiting.reason == 'CrashLoopBackOff':
                            crash_loop_backoff = True
                        if container_status.last_state and container_status.last_state.terminated:
                            if container_status.last_state.terminated.reason == 'StartError':
                                crash_loop_backoff = True
            except Exception as e:
                is_ready = False
                crash_loop_backoff = False                
            pods_info_list.append({
                'pod_name': pod.metadata.name,
                'infer_device': infer_device,
                'status': pod_status,
                'is_ready': is_ready,
                'crash_loop_backoff': crash_loop_backoff,
                'reserve': reserve,
            })    
    except ApiException as e:
        print(f"Exception when getting pods for deployment {deployment_name}: {e}")
    if pods_info_list == []:
        return None
    return pd.DataFrame(pods_info_list)

async def delete_pods(namespace):
# delete pod if pod is crashed
    status_skip = ['Running', 'Pending']
    try:
        pods = v1_core.list_namespaced_pod(namespace)
    except ApiException as e:
        print(f"Exception when calling CoreV1Api->list_namespaced_pod: {e}")
        return
    for pod in pods.items:
        if not pod.status.container_statuses:
            return
        for container_status in pod.status.container_statuses:
            if container_status.state.waiting and container_status.state.waiting.reason == 'CrashLoopBackOff':
                print(f"Pod {pod.metadata.name} is not running. Deleting...")
                try:
                    v1_core.delete_namespaced_pod(pod.metadata.name, namespace)
                    print(f"Pod {pod.metadata.name} deleted.")
                except ApiException as e:
                    print(f"Exception when calling CoreV1Api->delete_namespaced_pod: {e}")


if __name__ == "__main__":
    deployment_pods = list_deployments_and_pods()
    print(deployment_pods)
