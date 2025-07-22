import time,sys,os
from prometheus_api_client import PrometheusConnect
from kubernetes import client, config
from kubernetes.client.rest import ApiException

import asyncio
import pandas as pd
import math
import subprocess
import numpy as np

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'utils')))

from perfering_utils import GPUMetric, CPUMetric
from shuffle_utils_still import *
from openfaas_utils import *

deployment_pods = list_deployments_and_pods()
print(deployment_pods)


from kubernetes.client import (
    V1HorizontalPodAutoscaler,
    V1HorizontalPodAutoscalerSpec,
    V1CrossVersionObjectReference,
)



# Initialize the Kubernetes API client
kube_client = client.AppsV1Api()
autoscaling_client = client.AutoscalingV1Api()
# Constants

SCALE_UP_COOLDOWN = 1 * 60  # 1 minutes
DEFAULT_NUM_REPLICAS = 1  # Default number of replicas for each deployment
MAX_REPLICAS = 4  # Maximum number of replicas for each deployment
RESERVERD_POD_NUM = 1  # Number of reserved pods for each deployment


scale_records = {}



def calculate_desired_replicas(latency, rps, cv, queue_length, cur_scale, scale_threshold=1.5, cv_threshold=0.2):
    replica_capacity = 5 / latency
    total_capacity = cur_scale * replica_capacity
    load = rps + queue_length / latency
    desired_replicas = math.ceil(load / replica_capacity)
    if load > total_capacity * scale_threshold:
        return max(desired_replicas, cur_scale + 1)
    if load < total_capacity and cv < cv_threshold:
        return max(desired_replicas, 1)
    return cur_scale

def predict_execution_time(b, c_i, g_i, k):
    df = pd.read_csv(
        "infer_time_theoretical.csv",
    )
    filtered_df = df[
        (df["model"] == k)
        & (df["cpu"] == c_i)
        & (df["gpu"] == g_i)
        & (df["batch_size"] == b)
        & (df["device"] == "cuda")
    ]
    if len(filtered_df) == 0:
        return 0.3
    else:
        return filtered_df.iloc[0]["avg_response"]


def cacluate_need_replicas(deployment_name, namespace):
    current_scale = kube_client.read_namespaced_deployment_scale(
        deployment_name, namespace
    )
    rps = get_current_qps(deployment_name, namespace)
    if rps == 0:
        # print(f"{deployment_name} no requests")
        pass
        # return 0
    cv = get_cv(deployment_name, namespace)
    throughput = get_throughput(deployment_name, namespace)
    queue_length = get_queue_length(deployment_name, namespace)
    avg_rps = get_avg_qps(deployment_name, namespace)
    latency = therory.get(deployment_name, 0.02)
    cur_scale = current_scale.spec.replicas

    pods_of_deployment = get_pods_for_deployment(deployment_name, namespace)
    if pods_of_deployment is None or pods_of_deployment.empty:
        print(f"No pods found for {deployment_name}")
        reserve_counts = 0
        ready_pods_count = 0
        reserve_pods = None
        ready_pods = None
    else:
        reserve_pods = pods_of_deployment[
            (pods_of_deployment["is_ready"] == False)
            & (pods_of_deployment["crash_loop_backoff"] != True)
            & (pods_of_deployment["reserve"] == True)
        ]
        reserve_counts = reserve_pods.shape[0]
        ready_pods = pods_of_deployment[pods_of_deployment["is_ready"] == True]
        ready_pods_count = ready_pods.shape[0]
        ready_but_reserve_pods = pods_of_deployment[
            (pods_of_deployment["is_ready"] == True)
            & (pods_of_deployment["reserve"] == True)
        ]
        
    
    desired_replicas = calculate_desired_replicas(latency, rps, cv, queue_length, ready_pods_count)
    desired_replicas = min(desired_replicas, MAX_REPLICAS)
    if rps == 0:
        # continue
        desired_replicas = math.ceil(ready_pods_count / 2)
    if desired_replicas == 0:
        desired_replicas = 1
    return desired_replicas, ready_pods_count, reserve_counts, ready_pods, reserve_pods,ready_but_reserve_pods


last_check_time = {}


async def check_reserve_enough(deployment_name, namespace):
    if (
        last_check_time.get(deployment_name)
        and time.time() - last_check_time[deployment_name] < 20
    ):
        return
    last_check_time[deployment_name] = time.time()
    pods_of_deployment = get_pods_for_deployment(deployment_name, namespace)
    if pods_of_deployment is None or pods_of_deployment.empty:
        print(f"No pods found for {deployment_name}")
        reserve_counts = 0
        ready_pods_count = 0
    else:
        ready_pods_count = pods_of_deployment[
            pods_of_deployment["is_ready"] == True
        ].shape[0]
        reserve_pods = pods_of_deployment[
            (pods_of_deployment["is_ready"] == False)
            & (pods_of_deployment["crash_loop_backoff"] != True)
            # & (pods_of_deployment["infer_device"] == "cpu")
        ]
        reserve_counts = reserve_pods.shape[0]
        # print(f"reserve_counts for {deployment_name} is {reserve_counts}")
    if reserve_counts < RESERVERD_POD_NUM:
        # print(f"reserve {deployment_name} to {ready_pods_count + RESERVERD_POD_NUM}")
        await scale_deployment(
            deployment_name, namespace, ready_pods_count + RESERVERD_POD_NUM
        )

    return reserve_counts, ready_pods_count

therory = {
    "llama2-7b": 0.6,
    "opt-1dot3b": 0.3,
    "whisper-v2": 0.26,
    "labse": 0.16,
    "bert": 0.06,
    "bert-qa": 0.07,
    "resnet-18": 0.07,
    "resnet-50": 0.07,
    "resnet-152": 0.1,
    "vggnet-11": 0.09,
}


async def scale_shuffle(namespace):
    # Get all deployments in the namespace
    deployments = kube_client.list_namespaced_deployment(namespace)
    for deployment in deployments.items:
        deployment_name = deployment.metadata.name
        desired_replicas, ready_pods_count, reserve_counts, ready_pods, reserve_pods,ready_but_reserve_pods = (
            cacluate_need_replicas(deployment_name, namespace)
        )
        await check_reserve_enough(deployment_name, namespace)

        need_replicas = desired_replicas - ready_pods_count
        target_pods = None
        if need_replicas == 0:  # pass
            # print(f"Skipping for {deployment_name} due to already at desired replicas")
            pass
        elif need_replicas < 0:  # scale down
            # Check if we've scaled this deployment in the last 10 minutes
            last_scale_time = scale_records.get(deployment_name)
            if last_scale_time and time.time() - last_scale_time < SCALE_UP_COOLDOWN:
                # It's been less than 10 minutes since we last scaled this deployment, so skip this cycle
                print(
                    f"Skip {deployment_name} from {ready_pods_count} to {desired_replicas} due to in cooldown left {SCALE_UP_COOLDOWN - (time.time() - last_scale_time)} s"
                )
                continue
            else:
                print(f"scaled down {deployment_name} from {ready_pods_count} to {desired_replicas}")
                # Scale down
                target_pods = ready_but_reserve_pods.iloc[need_replicas:]["pod_name"].tolist()

                operate_pod_api(
                    namespace="openfaas-fn",
                    operate="notready",
                    target_pods=target_pods,
                )
        else:  # scale up
            target_pods = None
            if reserve_counts > need_replicas:
                target_pods = reserve_pods.iloc[:need_replicas]["pod_name"].tolist()
            elif reserve_counts > 0:
                target_pods = reserve_pods["pod_name"].tolist()
            else:  # no spare pods
                pass
            if target_pods:
                print(f"scale up {deployment_name} to {desired_replicas}")
                operate_pod_api(
                        namespace="openfaas-fn",
                        operate="ready",
                        target_pods=target_pods,
                    )
                if deployment_name in CPU_Parallel_Sensitive:  
                    operate_pod_api(
                        namespace="openfaas-fn",
                        operate="ready",
                        target_pods=target_pods,
                    )
                else: 
                    operate_pod_api(
                        namespace="openfaas-fn",
                        operate="cuda",
                        target_pods=target_pods,
                    )
                    operate_pod_api(
                        namespace="openfaas-fn",
                        operate="ready",
                        target_pods=target_pods,
                    )
            else:
                print(f"No reserve pods for {deployment_name}")
        asyncio.create_task(check_reserve_enough(deployment_name, namespace))
        scale_records[deployment_name] = time.time()


async def check_and_scale(namespace):
    # Retrieve all deployments in the namespace
    # try:
    deployments = kube_client.list_namespaced_deployment(namespace)
    if not deployments.items:
        print(f"No deployments found in namespace {namespace}")
        return
    await scale_shuffle(namespace)



while True:
    asyncio.sleep(5)
    asyncio.run(check_and_scale("openfaas-fn"))

