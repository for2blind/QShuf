from kubernetes import client, config
from kubernetes.client.rest import ApiException
import subprocess
from kubernetes.client import V1HorizontalPodAutoscaler, V1HorizontalPodAutoscalerSpec, V1CrossVersionObjectReference
from prometheus_api_client import PrometheusConnect
from cachetools import TTLCache,cached
import datetime

from depoly_function import FunctionManager
import os

REDIS_KEY='REGS'
root_path = 'QShuf/workloads/models'
current_dic = os.path.dirname(os.path.abspath(__file__))
os.chdir(current_dic)
manager = FunctionManager(root_path)



class KubernetesInstance:
    def __init__(self, kubeconfig_path):
        self.config_path = f'{current_dic}/{kubeconfig_path}'
        config.load_kube_config(config_file=self.config_path)
        self.v1 = client.CoreV1Api()
        self.client = client.AppsV1Api()
        self.autoscaling = client.AutoscalingV1Api()
        self.prometheus = PrometheusConnect(url=f"http://prometheus.example", disable_ssl=True)
        self.openfaas_prometheus = PrometheusConnect(url=f"http://prometheus.example", disable_ssl=True)
        self.master_ip = f'prometheus.example'
        
    
    def get_cluster_operator(self):
        return self.v1, self.client, self.autoscaling, self.prometheus, self.openfaas_prometheus

    def list_pods(self, namespace="openfaas-fn"):
        return self.v1.list_namespaced_pod(namespace=namespace)

    # @cached(cache=TTLCache(maxsize=1024, ttl=5))
    def list_deployments(self, namespace="openfaas-fn"):
        return self.client.list_namespaced_deployment(namespace=namespace)
    
    def read_namespaced_deployment_replicas(self, deployment_name, namespace="openfaas-fn"):
        try:
            self.client.read_namespaced_deployment(deployment_name, namespace)
        except:
            return 0

        return self.client.read_namespaced_deployment(deployment_name, namespace).spec.replicas 
    
    def list_namespace_pods(self, namespace='openfaas-fn'):
        return self.v1.list_namespaced_pod(namespace=namespace)


    def remove_dead_pod(self, namespace='openfaas-fn'):
        pods = self.list_namespace_pods()
        for dlp in pods.items:
            pod_name = dlp.metadata.name
            try:
                pod = self.v1.read_namespaced_pod(pod_name, namespace)
                if not pod.spec.containers[0].resources.limits.get('nvidia.com/gpu'):
                    return
                if not pod.status.container_statuses or not pod.status.container_statuses[0].state.running:
                    os.system(f'kubectl --kubeconfig={current_dic}/{self.config_path} delete pod {pod.metadata.name} -n {namespace}')
            except:
                pass

    def count_pendding_pods_number(self, namespace='openfaas-fn'):
        try:
            pendding_numbers = 0
            pods = self.list_namespace_pods()
            for pod in pods.items:
                status = pod.status.phase
                start_time_to_now = datetime.datetime.now() - pod.status.start_time.replace(tzinfo=None)
                if status == 'Pending' and start_time_to_now.seconds > 20:
                    pendding_numbers += 1
            return pendding_numbers
        except:
            return 0


    def scale_deployment(self, deployment_name, namespace, num_replicas):
        # check if the deployment exists
        if num_replicas == 0:
            manager.delete_function(deployment_name, self.master_ip)
            return

        try:
            self.client.read_namespaced_deployment(deployment_name, namespace)
        except ApiException as e:
            print(f"Failed to get deployment {deployment_name}: {e}")
            # if 404
            if e.status == 404:
                print(f"Creating deployment {deployment_name}...")
                manager.deploy_function(deployment_name, self.master_ip)

        # Check current number of replicas
        try:
            current_deployment = self.client.read_namespaced_deployment(deployment_name, namespace)
            current_replicas = current_deployment.spec.replicas
            if current_replicas == num_replicas:
                # print(f"No need to scale {deployment_name}. Current replicas: {current_replicas}")
                return
        except:
            return

        # Scale up a specific deployment
        try:
            # Construct the command
            cmd_cd = f'cd {current_dic}'
            cmd = ["kubectl", f"--kubeconfig={self.config_path}","scale", "deployment", deployment_name, "--replicas", str(num_replicas), "--namespace", namespace]
            os.system(f'{cmd_cd} && {" ".join(cmd)}')
            # Run the command
            print(f"Successfully scaled deployment {deployment_name} to {num_replicas} replicas")
        except subprocess.CalledProcessError as e:
            print(f"Failed to scale deployment {deployment_name}: {e}")

    def get_deployment_replicas(self, deployment_name, v1):
        deployment = v1.read_namespaced_deployment(deployment_name, "default")
        return deployment.spec.replicas


    def get_total_gpu_compute_major(self):
        try:
            # Get a list of all nodes
            nodes = self.v1.list_node()
            total_gpu_compute_major = 0

            # Iterate through the nodes and sum the 'nvidia.com/gpu.compute.major' value
            for node in nodes.items:
                gpu_compute_major = node.metadata.labels.get('nvidia.com/gpu.compute.major', "0")
                total_gpu_compute_major += int(gpu_compute_major)

            return total_gpu_compute_major
        except:
            return None

    def get_not_ready_pod_number(self, namespace='openfaas-fn'):
        try:
            pods = self.list_namespace_pods(namespace=namespace)
            function_not_ready_numbers = {}
            for pod in pods.items:
                pod_name = pod.metadata.name
                status = pod.status.container_statuses[0].ready
                start_time_to_now = datetime.datetime.now() - pod.status.start_time.replace(tzinfo=None)

                if not status and start_time_to_now.seconds > 120:
                    function_name = '-'.join(pod_name.split('-')[0:-2])
                    function_not_ready_numbers[function_name] = function_not_ready_numbers.get(function_name, 0) + 1
            
            return function_not_ready_numbers
        except:
            return {}

            
    def get_pod_to_gpu_map(self, namespace='openfaas-fn'):
        function_to_gpu = {}
        model_to_gpu = {}
        used_gpu = []
        try:
            pods = self.list_namespace_pods(namespace=namespace)
            for pod in pods.items:
                function_name = pod.metadata.labels.get('faas_function')
                model_name = '-'.join(function_name.split('-')[0:2])
                for env_var in pod.spec.containers[0].env:
                    if env_var.name == 'NVIDIA_VISIBLE_DEVICES':
                        device_uuid = env_var.value
                        used_gpu.append(device_uuid)
                        break
                function_to_gpu.setdefault(function_name, []).append(device_uuid)
                model_to_gpu.setdefault(model_name,[]).append(device_uuid) 
            return function_to_gpu, model_to_gpu, list(used_gpu)
        except Exception as e:
            print(e)
            return None            






