import os, datetime, time
import pandas as pd

current_path = os.path.dirname(os.path.abspath(__file__))
os.chdir(current_path)
import kubernetes
import uuid
import subprocess, asyncio, requests
from concurrent.futures import ProcessPoolExecutor
from collections import defaultdict
import pytz

kubernetes.config.load_kube_config()
v1 = kubernetes.client.CoreV1Api()
namespace = "openfaas-fn"
scaling = "none"
scheduler = "ES1"
slo = 10
exp = "ES1"
mu = 0 
duration = 600
wrkname = "qshuffler"
benchmark_version = "1.0"
columns = [
    "uuid",
    "wrkname",
    "scaling",
    "scheduler",
    "start_time",
    "end_time",
    "slo",
    "cv",
    "collected",
    "csv",
]
evaluation_record_path = (
    f"QShuf/evaluation/{exp.split('_')[0]}/evaluation_record/record_{exp}.csv"
)
if not os.path.exists(evaluation_record_path):
    if not os.path.exists(
        f"QShuf/evaluation/{exp.split('_')[0]}/evaluation_record/"
    ):
        os.makedirs(
            f"QShuf/evaluation/{exp.split('_')[0]}/evaluation_record/"
        )
    evaluation_record = pd.DataFrame(columns=columns)
    evaluation_record.to_csv(evaluation_record_path, index=False)
evaluation_record = pd.read_csv(evaluation_record_path, names=columns)

wrknames = {
    "ES1": [
        "qshuffler",
    ]
}
entry = {}
base_url = "http://openfaas.example/function/"

url_mapping = {
    "bert": "http://openfaas.example/function/bert.openfaas-fn",
    "bert-qa": "http://openfaas.example/function/bert-qa.openfaas-fn",
    "labse": "http://openfaas.example/function/labse.openfaas-fn",
    "llama2-7b": "http://openfaas.example/function/llama2-7b.openfaas-fn",
    "resnet-18": "http://openfaas.example/function/resnet-18.openfaas-fn",
    "resnet-50": "http://openfaas.example/function/resnet-50.openfaas-fn",
    "resnet-152": "http://openfaas.example/function/resnet-152.openfaas-fn",
    "vggnet-11": "http://openfaas.example/function/vggnet-11.openfaas-fn",
    "whisper-v2": "http://openfaas.example/function/whisper-v2.openfaas-fn",
    "opt-1dot3b": "http://openfaas.example/function/opt-1dot3b.openfaas-fn",
}




def check_if_service_ready(model, wrkname):
    url_list = url_mapping.copy()
    url = url_list[model]
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            print(f"service of {model} is ready")
            return True
        else:
            print(f"service of {model} is not ready, return: {r.status_code}, {r.text}")
            return False
    except Exception as e:
        print(f"service of {model} is error", e)
        return False


# using request test if the wrk is ready
async def check_wrk_service_ready(wrkname):
    models = list(url_mapping.keys())
    while True:
        for model in models:
            print(f"check if {model} is ready")
            status = check_if_service_ready(model, wrkname)
            if status == True:
                models.remove(model)
        if len(models) == 0:
            print(f"All service in wrk {wrkname} is ready")
            break


async def remove_dead_pod(pod_name):
    try:
        pod = v1.read_namespaced_pod(pod_name, namespace)
        if not pod.spec.containers[0].resources.limits.get("nvidia.com/gpu"):
            return
        if (
            not pod.status.container_statuses
            or not pod.status.container_statuses[0].state.running
        ):
            os.system(f"kubectl delete pod {pod.metadata.name} -n {namespace}")
    except:
        pass


async def check_wrk_pod_ready(wrkname, namespace):
    while True:
        pod_status = {}
        pods = v1.list_namespaced_pod(namespace).items
        for pod in pods:
            if pod.status.container_statuses:
                pod_status[pod.metadata.name] = pod.status.container_statuses[0].ready
            else:
                pod_status[pod.metadata.name] = False

        pod_ready = all(status for status in pod_status.values())
        # delete the pod of status "CrashLoopBackOff"
        if not pod_ready:
            print(f"{datetime.datetime.now()}, pod of {wrkname} is not yet ready")
            for pod in pod_status:
                if not pod_status[pod]:
                    await remove_dead_pod(pod)
        else:
            print(f"{wrkname} is ready")
            return pod_ready
        print(f"wait for 30 seconds to check again")
        await asyncio.sleep(30)


async def start_request_to_wrks(wrkname, scaling, scheduler, cv, mu, duration):
    evaluation_record = pd.read_csv(evaluation_record_path, names=columns)
    if (
        len(
            evaluation_record[
                (evaluation_record["wrkname"] == wrkname)
                & (evaluation_record["scaling"] == scaling)
                & (evaluation_record["scheduler"] == scheduler)
                & (evaluation_record["cv"] == cv)
            ]
        )
        > 0
    ):
        print(f"{wrkname} {scaling} {scheduler} {cv} has been evaluated")
        # return
    start_datetime = datetime.datetime.now(pytz.timezone("Asia/Shanghai"))
    estimated_end_time = start_datetime + datetime.timedelta(seconds=duration)
    print(f"Workload started at: {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Estimated end time: {estimated_end_time.strftime('%Y-%m-%d %H:%M:%S')}")

    start_time = time.time().__int__()
    # print("Usage: python3 round_robbin_wrk.py  <wrkname> <scaling> <scheduler> <slo> <start_time>")
    cmd = f"python3 cv_wula_mu_{exp.split('_')[0]}.py {wrkname} {scaling} {scheduler} {slo} {start_time} {exp} {cv} {mu} {duration}"
    print("run", cmd)
    process = subprocess.Popen(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    process.wait()
    stdout, stderr = process.communicate()
    print(stdout.decode(), stderr.decode())
    end_time = time.time().__int__()
    evaluation_uuid = uuid.uuid4()
    resp_path = f"../wula_result/{exp}/{scheduler}/{start_time}/"
    tmp_record = pd.DataFrame(
        [
            [
                evaluation_uuid,
                wrkname,
                scaling,
                scheduler,
                start_time,
                end_time,
                slo,
                cv,
                0,
                resp_path,
            ]
        ],
        columns=columns,
    )
    evaluation_record = pd.concat([evaluation_record, tmp_record])
    evaluation_record.to_csv(evaluation_record_path, index=False, header=False)


def wait_until_no_pods(namespace="openfaas-fn", check_interval=5):
    while True:
        pod_list = v1.list_namespaced_pod(namespace=namespace)
        pods = pod_list.items
        # active_pods = [pod for pod in pods if pod.metadata.deletion_timestamp is None]
        active_pods = pods

        if not active_pods:
            return True

        time.sleep(check_interval)


async def release_model(wrkname):
    cmd = f"kubectl scale deployment --replicas=0 --namespace=openfaas-fn --all"
    # cmd = f'bash bash_delete.sh'
    os.system(cmd)
    await asyncio.sleep(15)
    wait_until_no_pods(namespace)
    return True


async def deploy_model(scheduler):
    cmd = (
        f"bash QShuf/evaluation/{exp.split('_')[0]}/deploy_{scheduler}.sh"
    )
    print(cmd)
    os.system(cmd)
    await asyncio.sleep(60)
    return True


def build_url_list(wrkname):
    return url_mapping


async def run_wrk_benchmark(wrkname, scheduler):
    if (
        len(
            evaluation_record[
                (evaluation_record["wrkname"] == wrkname)
                & (evaluation_record["scaling"] == scaling)
                & (evaluation_record["scheduler"] == scheduler)
            ]
        )
        > 0
    ):
        print(f"{wrkname} has been evaluated")
        # return
    CVs = [0.1, 1, 2, 4, 8]  # for different workload.
    duration = 600
    # build request distribution
    for cv in CVs:
        try:
            await release_model(wrkname)
            await deploy_model(scheduler=scheduler)
            print(f"{wrkname} is deployed, sleeping to function ready!")
            await asyncio.sleep(60)

            # #### await replace_retry_service(wrkname)
            if "dynamic" not in scheduler:
                await check_wrk_pod_ready(wrkname, namespace)

            # await asyncio.sleep(68)
            await check_wrk_service_ready(wrkname)
            print(f"all service of {wrkname} is ready, start benchmark!")
            await start_request_to_wrks(wrkname, scaling, scheduler, cv, mu, duration)
            print(f"{wrkname} {cv}done")
            await asyncio.sleep(30)
            await release_model(wrkname)
        except Exception as e:
            print(f"error {e}")
            return e


import psutil, sys, signal

global process_scale, process_schedule


def terminate_process(proc):
    parent = psutil.Process(proc.pid)
    for child in parent.children(recursive=True):
        child.terminate()
    parent.terminate()
    gone, still_alive = psutil.wait_procs([parent] + parent.children(), timeout=5)
    for p in still_alive:
        p.kill()
    print("Process and its children have been terminated.")


def signal_handler(sig, frame):
    print("Interrupt received, terminating processes...")
    if "process_scale" in globals() and process_scale.poll() is None:
        terminate_process(process_scale)
    if "process_schedule" in globals() and process_schedule.poll() is None:
        terminate_process(process_schedule)
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    wrkname = "qshuffler"
    os.system(f"kubectl scale deployment --replicas=0 --namespace=openfaas-fn --all")
    print("start scale")
    process_scale = subprocess.Popen(
            ["python3", f"QShuf/src/scaler/scaler.py"])
    process_schedule = subprocess.Popen(
        ["python3", f"QShuf/src/scheduler/scheduler.py"]
    )
    asyncio.run(run_wrk_benchmark(wrkname, scheduler))
    
    time.sleep(10)
