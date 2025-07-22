import pandas as pd
from datetime import datetime
import os, sys, subprocess, math
import numpy as np
import logging, time
from cachetools import TTLCache, cached
import asyncio
from collections import defaultdict
import multiprocessing

current_path = os.path.dirname(os.path.abspath(__file__))
os.chdir(current_path)

logging.basicConfig(filename="cv_wula.log", level=logging.DEBUG)
GATEWAY = "http://openfaas.example/"
namespace = "openfaas-fn"

exp = "ES1_5"

mu_theory = {
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


url_list = {
    "llama2-7b": "http://openfaas.example/function/llama2-7b.openfaas-fn",
    "whisper-v2": "http://openfaas.example/function/whisper-v2.openfaas-fn",
    "opt-1dot3b": "http://openfaas.example/function/opt-1dot3b.openfaas-fn",   
    "vggnet-11": "http://openfaas.example/function/vggnet-11.openfaas-fn",
    "resnet-152": "http://openfaas.example/function/resnet-152.openfaas-fn",
    "resnet-50": "http://openfaas.example/function/resnet-50.openfaas-fn",
    "bert-qa": "http://openfaas.example/function/bert-qa.openfaas-fn",
    "resnet-18": "http://openfaas.example/function/resnet-18.openfaas-fn",
    "labse": "http://openfaas.example/function/labse.openfaas-fn",    
    "bert": "http://openfaas.example/function/bert.openfaas-fn",
}


def build_url_list(wrkname):
    return url_list


models = list(url_list.keys())


def generate_gamma_distribution(t: float, mu: float, sigma: float):
    beta = sigma**2 / mu
    alpha = mu / beta
    n = int(math.ceil(t / mu))
    s = t * 2
    rng = np.random.default_rng(int(time.time_ns() % (2**32)))
    # current_time_ms = int(time.time() * 1000)
    # np.random.seed(current_time_ms % (2**32))
    while s > t * 1.5:
        # dist = np.random.gamma(alpha, beta, n)
        dist = rng.gamma(alpha, beta, n)
        for i in range(1, n):
            dist[i] += dist[i - 1]
        s = dist[-1]
    return dist


def to_file(distFile: str, dist: list[float]):
    if os.path.exists(distFile):
        return
    os.makedirs(os.path.dirname(distFile), exist_ok=True)
    with open(distFile, "w+") as f:
        for d in dist:
            f.write(f"{d}\n")


def build_request_distribution(
    wrkname: str,
    benchmark: str,
    scheduler: str,
    slo: str,
    start_time: str,
    mu: float,
    cv: float,
    duration: int,
):
    sigma = mu * cv
    output_path = f"../workloads/cvd/{wrkname}/{benchmark}/{cv}_{mu}_{duration}/"
    if not os.path.exists(output_path):
        os.makedirs(output_path)
    dist = generate_gamma_distribution(duration, mu, sigma)
    to_file(os.path.join(output_path, f"{benchmark}-dist.txt"), dist)


async def run_http_request_with_wula(
    wrkname: str,
    benchmark: str,
    scheduler: str,
    slo: str,
    start_time: str,
    mu: float,
    cv: float,
    url: str,
    synctime: str,
    duration: int,
):
    resp_path = f"../metrics/wula/{exp}/{wrkname}/{scheduler}/{start_time}/{cv}_{mu}_{duration}/"
    cvd_path = f"../workloads/cvd/{wrkname}/{benchmark}/{cv}_{mu}_{duration}/"

    os.makedirs(os.path.dirname(resp_path), exist_ok=True)
    request_cmd = f"../wula -name {benchmark} -dist {benchmark} -dir {cvd_path} -dest {resp_path}/{benchmark}.csv -url {url} -SLO {slo} -synctime {synctime}"
    print(request_cmd)
    logging.debug(request_cmd)
    process = await asyncio.create_subprocess_shell(request_cmd)
    await process.wait()


def get_mu(input_substring):
    # return 0.016
    try:
        mu = float(mu_theory[input_substring.strip().lower()])
        return mu
    except Exception as e:
        logging.debug(e)
        print(e)
        return 0.5


duration = 600


async def main(cv):
    benchmark_entry = build_url_list(wrkname)
    tasks = []
    base_synctime = int((time.time() + 10) * 1000)

    for i, (benchmark, url) in enumerate(benchmark_entry.items()):
        print(benchmark)
        if not any(model.lower() in benchmark.lower() for model in models):
            print(f"skip {benchmark}")
            continue
        mu_value = get_mu(benchmark)
        mu = round(mu_value, 2)
        build_request_distribution(
            wrkname, benchmark, scheduler, slo, start_time, mu, cv, duration
        )

        synctime = base_synctime + i *20 * 1000

        print(f"{benchmark} {cv} {mu} {url} synctime={synctime}")
        logging.debug(f"{benchmark} {cv} {mu} {url} synctime={synctime}")

        tasks.append(
            run_http_request_with_wula(
                wrkname,
                benchmark,
                scheduler,
                slo,
                start_time,
                mu,
                cv,
                url,
                synctime,
                duration,
            )
        )

    await asyncio.gather(*tasks)
    await asyncio.sleep(10)
    # cmd = f"kubectl scale deployment --replicas=1 --namespace=openfaas-fn --all"
    # cmd = f'bash bash_delete.sh'
    # os.system(cmd)
    await asyncio.sleep(60)


increment = 10

if __name__ == "__main__":

    start_time = time.time().__int__()
    default_values = ["cv_wula.py", "ME", "none", "dasheng", "10", start_time, exp, 8]
    args = sys.argv
    if len(args) > 1:
        args = sys.argv
    else:
        args = default_values
    if len(args) == 0:
        print(
            "Usage: python3 cv_wula.py  <wrkname> <scaling> <scheduler> <slo> <start_time> <exp>"
        )
        # exit(1)
    wrkname = args[1]
    scaling = args[2]
    scheduler = args[3]
    slo = args[4]
    start_time = args[5]
    exp = args[6]
    cv = float(args[7])
    # mu = float(args[8])
    duration = int(args[9])
    logging.debug(
        f"==========================================\n \
        wrkname: {wrkname}, scaling: {scaling}, scheduler: {scheduler}, slo: {slo}, start_time: {start_time}, exp: {exp}, cv: {cv}"
    )
    print(args)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    print(f"start time cv{cv} ", start_time)
    loop.run_until_complete(main(cv))  # <-- Use this instead
    loop.close()  # <-- Stop the event loop
    exit(0)
