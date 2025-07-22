from kubernetes import client, config
import re
from utils.perfering_utils import GPUMetric, CPUMetric

from fastapi.responses import JSONResponse
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Depends
import json
import ssl, os, sys
import base64
import pandas as pd
from Kuberinit import KubernetesInstance
import pandas as pd
import asyncio

print("Current Directory:", os.getcwd())
print("System Path:", sys.path)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'utils')))

from shuffle_utils import *

deployment_pods = list_deployments_and_pods()
print(deployment_pods)

namespace = "openfaas-fn"
context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
context.load_cert_chain(f"hook/{namespace}_server.crt", f"hook/{namespace}_server.key")

from prometheus_api_client import PrometheusConnect


prometheus = PrometheusConnect(url="http://prometheus.example")
cluster_config = {
    "cluster": {
        "master_ip": "",
        "kubeconfig_path": "config_c1",
        "prometheus_port": 80,
        "price": 0.019,
    }
}
min_unreserved_pod_num = 1
RESERVED_POD_NUM = 1
kinstance = KubernetesInstance(
    cluster_config["cluster"]["kubeconfig_path"], cluster_config["cluster"]["master_ip"]
)
priority_scores = {}
# Load the Kubernetes configuration
config.load_kube_config()
# Initialize the Kubernetes API client
api = client.CoreV1Api()
v1 = client.AppsV1Api()
scheduler_name = "qscheduler"
gpu_metric = GPUMetric(prometheus)
cpu_metric = CPUMetric(prometheus)
# function_to_gpu_map,model_to_gpu_map= kinstance.get_pod_to_gpu_map()
reserved_cpu = 0.3
GPU_QUOTA = 1.15
memory_units = {
    "K": 1024**2,
    "M": 1024**1,
    "G": 1,
}

node_list = {}
node_include = []
node_cpu_include = []
node_exclude = []


def get_cpu_gpu():
    gpu_pd = gpu_metric.hard_get_gpu(prometheus)
    cpu_pd = cpu_metric.hard_get_cpu(prometheus)
    merged_pd = pd.merge(
        gpu_pd, cpu_pd, left_on="node_name", right_on="node_name", how="inner"
    )
    return merged_pd


spare_node = get_cpu_gpu()
spare_node.to_csv("spare_node.csv")
spare_node = spare_node[
    spare_node["node_name"].isin(node_include)
    & ~spare_node["node_name"].isin(node_exclude)
]
print(spare_node)

# node_name, device_uuid, cpu_nums, cuda, reserve
schedule_rule_dynamic = {

}

index_count = {}


def get_index(deployment_name):
    if deployment_name not in index_count:
        index_count[deployment_name] = 0
        print(f"Initialize index_count for {deployment_name}")
    else:
        index_count[deployment_name] += 1
        print(f"Update index_count for {deployment_name}")
    if index_count[deployment_name] >= len(schedule_rule_dynamic[deployment_name]):
        print(f"Reset index_count for {deployment_name}")
        index_count[deployment_name] %= len(schedule_rule_dynamic[deployment_name])
    return index_count[deployment_name]


def schedule(pod):
    # Get function from pod's labels
    deployment_name = pod["metadata"]["labels"].get("faas_function")
    if deployment_name not in deployment_name:
        raise ValueError(f"Unknown deployment name: {deployment_name}")
    node_index = get_index(deployment_name)
    node_name, device_uuid, cpu_nums, cuda, reserve = schedule_rule_dynamic[
        deployment_name
    ][node_index]
    print(f"deployment_name: {deployment_name}, node_index: {node_index}")
    print(
        f"node_name: {node_name}, device_uuid: {device_uuid}, cpu_nums: {cpu_nums}, cuda: {cuda}, reserve: {reserve}"
    )
    record = pd.DataFrame(
        [
            [
                pd.Timestamp.now(),
                pod["metadata"]["generateName"],
                device_uuid,
                node_name,
                cpu_nums,
                cuda,
                reserve,
            ]
        ],
        columns=[
            "time",
            "pod_name",
            "GPU_uuid",
            "node",
            "cpu_nums",
            "cuda",
            "reserve",
        ],
    )
    record.to_csv("scheduler_allcuda_record.csv", mode="a", header=False, index=False)
    return node_name, device_uuid, cpu_nums, cuda, reserve


def patch_pod(
    deployment_name, pod, node_name, device_uuid, cpu_nums, cuda=True, reserve=False
):
    patch_operation = [
        # pod name
        {
            "op": "add",
            "path": "/spec/nodeSelector",
            "value": {"kubernetes.io/hostname": node_name},
        },
        {
            "op": "add",
            "path": "/spec/containers/0/volumeMounts",
            "value": [
                {
                    "name": "dataset-QShuf",
                    "mountPath": "/dataset/QShuf/",
                },
            ],
        },
        {
            "op": "add",
            "path": "/spec/volumes",
            "value": [
                {
                    "name": "dataset-QShuf",
                    "hostPath": {"path": "/sharing/model/QShuf"},
                },
            ],
        },
    ]
    patch_operation.append(
        {
            "op": "add",
            "path": "/spec/containers/0/readinessProbe",
            "value": {
                "httpGet": {"path": "/loaded", "port": 5001},
                "initialDelaySeconds": 1,
                "periodSeconds": 1,
            },
        }
    )
    existing_env = pod["spec"]["containers"][0].get("env", [])
    infer_device_value = "cuda" if cuda else "cpu"
    patch_operation.append(
        {
            "op": "add",
            "path": "/metadata/labels/infer_device",
            "value": infer_device_value,
        },
    )
    for env in existing_env:
        if env["name"] == "infer_device":
            env["value"] = infer_device_value
            break
    else:
        existing_env.append({"name": "infer_device", "value": infer_device_value})
    existing_env.append({"name": "reserve", "value": f"{reserve}"})
    existing_env.append({"name": "TIMEOUT_FACTOR", "value": "1.0"})
    patch_operation.append(
        {
            "op": "add",
            "path": "/metadata/labels/reserve",
            "value": f"{reserve}",
        },
    )
    if cuda:  # cuda
        patch_operation.append(
            {
                "op": "add",
                "path": "/spec/containers/0/env",
                "value": existing_env
                + [
                    {"name": "NVIDIA_VISIBLE_DEVICES", "value": device_uuid},
                    {"name": "CUDA_VISIBLE_DEVICES", "value": f"{device_uuid}"},
                ],
            },
        )
        patch_operation.append(
            {
                "op": "add",
                "path": "/spec/containers/0/resources",
                "value": {
                    "limits": {"nvidia.com/gpu": 6, "cpu": cpu_nums},  # 3
                    "requests": {"nvidia.com/gpu": 6, "cpu": cpu_nums},
                },
            },
        )
    else:  # cpu
        patch_operation.append(
            {
                "op": "add",
                "path": "/spec/containers/0/env",
                "value": existing_env,
            },
        )
        patch_operation.append(
            {
                "op": "add",
                "path": "/spec/containers/0/resources",
                "value": {
                    "limits": {"cpu": cpu_nums},
                    "requests": {"cpu": cpu_nums},
                },
            },
        )

    return base64.b64encode(json.dumps(patch_operation).encode("utf-8"))


def admission_response(
    deployment_name, uid, message, pod, node_name, device_uuid, cpu_nums, cuda, reserve
):
    if not node_name:
        return {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": {"uid": uid, "allowed": False, "status": {"message": message}},
        }
    # Create an admission response
    return {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {
            "uid": uid,
            "allowed": True,
            "status": {"message": message},
            "patchType": "JSONPatch",
            "patch": patch_pod(
                deployment_name, pod, node_name, device_uuid, cpu_nums, cuda, reserve
            ).decode("utf-8"),
        },
    }


app = FastAPI()


@app.post("/mutate")
async def mutate(request: Request):
    review = await request.json()
    pod = review["request"]["object"]
    deployment_name = pod["metadata"]["labels"].get("faas_function")
    print(f"Receive:{deployment_name}")
    node_name, device_uuid, cpu_nums, cuda, reserve = schedule(pod)
    if node_name:
        addmission = admission_response(
            deployment_name,
            review["request"]["uid"],
            "success",
            pod,
            node_name,
            device_uuid,
            cpu_nums,
            cuda,
            reserve,
        )
        return addmission
    else:
        return admission_response(
            deployment_name,
            review["request"]["uid"],
            "fail",
            pod,
            node_name,
            device_uuid,
            cpu_nums,
            cuda,
            reserve,
        )


if __name__ == "__main__":
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=9008,
        ssl_keyfile=f"hook/{namespace}_server.key",
        ssl_certfile=f"hook/{namespace}_server.crt",
    )
    print("Start scheduler")
    server = uvicorn.Server(config)
    server.run()

