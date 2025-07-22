
from prometheus_api_client import PrometheusConnect
import pandas as pd
prometheus_client = PrometheusConnect(
    url="http://prometheus.example/", disable_ssl=False
)

def get_cv(deployment_name, namespace):
    # stddev_over_time(sum(irate(gateway_function_invocation_started{function_name="opt-66b-submod-0-latency-64.openfaas-fn"}[60s]))by (function_name) [10m:1s])/avg_over_time(sum(irate(gateway_function_invocation_started{function_name="opt-66b-submod-0-latency-64.openfaas-fn"}[60s]))by (function_name) [10m:1s])
    cv_query = f'stddev_over_time(sum(irate(gateway_function_invocation_started{{function_name=~"{deployment_name}.{namespace}"}}[1m])) by (function_name) [10m:1s])/avg_over_time(sum(irate(gateway_function_invocation_started{{function_name=~"{deployment_name}.{namespace}"}}[1m])) by (function_name) [10m:1s])'
    result = prometheus_client.custom_query(query=cv_query)
    request_df = pd.DataFrame()
    for r in result:
        df = pd.DataFrame([r["value"]], columns=["time", "cv"])
        request_df = pd.concat([request_df, df])
    if request_df.empty:
        # print(f"Query {cv_query} empty")
        return 0
    request_df["cv"] = pd.to_numeric(request_df["cv"], errors="coerce")
    request_df["cv"].fillna(0, inplace=True)
    cv = float(request_df["cv"].mean())
    return cv


def get_queue_length(deployment_name, namespace):
    queue_query = f'sum by (function_name) (gateway_function_invocation_started{{function_name=~"{deployment_name}.{namespace}"}}) - sum by (function_name) (gateway_function_invocation_total{{function_name=~"{deployment_name}.{namespace}"}})'
    result = prometheus_client.custom_query(query=queue_query)
    request_df = pd.DataFrame()
    for r in result:
        df = pd.DataFrame([r["value"]], columns=["time", "queue"])
        request_df = pd.concat([request_df, df])
    if request_df.empty:
        print(f"Query {queue_query} empty")
        return 0
    request_df["queue"] = pd.to_numeric(request_df["queue"], errors="coerce")
    request_df["queue"].fillna(0, inplace=True)
    queue = float(request_df["queue"].mean())
    return queue


def get_current_qps(deployment_name, namespace):
    rps_query = f'sum(irate(gateway_function_invocation_started{{function_name=~"{deployment_name}.{namespace}"}}[1m])) by (function_name)'
    result = prometheus_client.custom_query(query=rps_query)
    request_df = pd.DataFrame()
    for r in result:
        df = pd.DataFrame([r["value"]], columns=["time", "rps"])
        request_df = pd.concat([request_df, df])
    if request_df.empty:
        # print(f"Query {rps_query} empty")
        return 0
    rps = float(request_df["rps"].sum())
    return rps


def get_avg_qps(deployment_name, namespace):
    # avg_over_time(sum(rate(gateway_function_invocation_started{function_name="opt-66b-submod-0-latency-64.openfaas-fn"}[60s]))[10m:1s])
    rps_query = f'avg_over_time(sum(irate(gateway_function_invocation_started{{function_name=~"{deployment_name}.{namespace}"}}[1m])) by (function_name) [10m:1s]) '
    result = prometheus_client.custom_query(query=rps_query)
    request_df = pd.DataFrame()
    for r in result:
        df = pd.DataFrame([r["value"]], columns=["time", "rps"])
        request_df = pd.concat([request_df, df])
    if request_df.empty:
        # print(f"Query {rps_query} empty")
        return 0
    request_df["rps"] = pd.to_numeric(request_df["rps"], errors="coerce")
    avg_rps = float(request_df["rps"].mean())
    return avg_rps


# def get_lantency():
#     latency = 1
#     return latency


def get_throughput(deployment_name, namespace):
    # avg_over_time(sum(rate(gateway_function_invocation_total{function_name="opt-66b-submod-0-latency-64.openfaas-fn",code="200"}[1m]))by (function_name) [5s:1s])
    throughput_query = f'avg_over_time(sum(irate(gateway_function_invocation_total{{function_name=~"{deployment_name}.{namespace}",code="200"}}[1m])) by (function_name) [5s:1s] )'
    result = prometheus_client.custom_query(query=throughput_query)
    request_df = pd.DataFrame()
    for r in result:
        df = pd.DataFrame([r["value"]], columns=["time", "throughput"])
        request_df = pd.concat([request_df, df])
    if request_df.empty:
        # print(f"Query {throughput_query} empty")
        return 0
    request_df["throughput"] = pd.to_numeric(request_df["throughput"], errors="coerce")
    throughput = float(request_df["throughput"].mean())
    return throughput
