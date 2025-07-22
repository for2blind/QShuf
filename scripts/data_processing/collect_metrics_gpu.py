from prometheus_api_client import PrometheusConnect
import datetime, time
import pandas as pd

# from kubernetes import client, config, watch

# read json config file from 'gss.json'
import json
import os, re
import sys, argparse

# config.load_kube_config()
import pytz
import glob

os.chdir(os.path.dirname(os.path.abspath(__file__)))


class MetricCollector:
    def __init__(self, kubernetes_nodes):
        self.prom = PrometheusConnect(url="http://prometheus.example")
        self.columns = ["timestamp", "value", "metrics", "instance", "ip"]
        self.columns_gpu = [
            "timestamp",
            "value",
            "metrics",
            "instance",
            "gpu",
            "modelName",
        ]
        self.columns_pod = ["timestamp", "value", "metrics", "function"]
        self.mdict_node_hybird = {
            "node_gpu_util": f'DCGM_FI_DEV_GPU_UTIL{{kubernetes_node=~"{kubernetes_nodes}"}} > 0',
            "gpu_DRAM_activated": f'DCGM_FI_PROF_DRAM_ACTIVE{{kubernetes_node=~"{kubernetes_nodes}"}} > 0',
            "gpu_tensor_pipe_active": f'DCGM_FI_PROF_PIPE_TENSOR_ACTIVE{{kubernetes_node=~"{kubernetes_nodes}"}} > 0',
            "gpu_men_util": f'DCGM_FI_DEV_MEM_COPY_UTIL{{kubernetes_node=~"{kubernetes_nodes}"}} > 0',
            "gpu_sm_active": f'DCGM_FI_PROF_SM_ACTIVE{{kubernetes_node=~"{kubernetes_nodes}"}} > 0',
        }
        self.mdict_pod = {
            "pod_cpu_util": 'avg(rate(container_cpu_usage_seconds_total{namespace="openfaas-fn"} [30s])) by(pod) /avg(kube_pod_container_resource_limits{resource="cpu",namespace="openfaas-fn"}) by (pod) *100',
            "pod_mem_usage": "avg(container_memory_rss+container_memory_cache+container_memory_usage_bytes+container_memory_working_set_bytes{namespace='openfaas-fn'}) by (pod)/1024/1024",
            "pod_cpu_usage": "rate(container_cpu_usage_seconds_total{namespace='openfaas-fn'} [60s])",
        }
        self.sum_metrics = {
            "active_gpu_num": "count(DCGM_FI_DEV_GPU_UTIL > 0)",
            "energy_consumption": "sum(rate(DCGM_FI_DEV_POWER_USAGE[60s]))",
            "avg_sm_active": "avg(DCGM_FI_PROF_SM_ACTIVE > 0)",
            "avg_sm_active_hybird": f'avg(DCGM_FI_PROF_SM_ACTIVE{{kubernetes_node=~"{kubernetes_nodes}"}})',
        }
        self.deploy_metrics = {
            "replica_num": 'sum (kube_deployment_status_replicas_available{namespace="openfaas-fn"})  by (deployment) ',
        }
        self.IB_traffic = {
            "IB_transmit": 'sum(rate(node_infiniband_port_data_transmitted_bytes_total{job="gpu-metrics"}[2s]))  by (kubernetes_node) > 1024',
            "IB_receive": 'sum(rate(node_infiniband_port_data_received_bytes_total{job="gpu-metrics"}[2s]))  by (kubernetes_node) > 1024',
        }
        self.cpu_metrics = {
            "cpu_usage": f'sum(rate(node_cpu_seconds_total{{kubernetes_node=~"{kubernetes_nodes}"}}[1m])) by (kubernetes_node)',
            "cpu_mem_usage": f'sum(rate(node_memory_Active_bytes{{kubernetes_node=~"{kubernetes_nodes}"}}[1m])) by (kubernetes_node) /1024/1024',
            "cpu_context_switches": f'sum(rate(node_context_switches_total{{kubernetes_node=~"{kubernetes_nodes}"}}[1m])) by (kubernetes_node)',
        }

    def collect_node_cpu_context_switches_metrics(self, start_time, end_time):
        start_time = datetime.datetime.fromtimestamp(start_time, tz=pytz.UTC)
        end_time = datetime.datetime.fromtimestamp(end_time, tz=pytz.UTC)
        Mdata_cpu_context_switch = pd.DataFrame(columns=self.columns)
        for metric_name in self.cpu_metrics.keys():
            query_result = self.prom.custom_query_range(
                self.cpu_metrics[metric_name],
                end_time=end_time,
                start_time=start_time,
                step="1s",
            )
            for m in query_result:
                data_t = pd.json_normalize(m)
                dk_tmp = pd.DataFrame(columns=self.columns)

                for i, r in data_t.iterrows():

                    df_values = pd.DataFrame(
                        r["values"], columns=["timestamp", "value"]
                    )
                    df_values["instance"] = r["metric.kubernetes_node"]

                    dk_tmp = pd.concat([dk_tmp, df_values], axis=0)
                dk_tmp["metrics"] = metric_name
                Mdata_cpu_context_switch = pd.concat(
                    [Mdata_cpu_context_switch, dk_tmp], axis=0
                )
            print(f"cpu : {metric_name}")
        return Mdata_cpu_context_switch

    def collect_node_IB_metrics(self, start_time, end_time):
        start_time = datetime.datetime.fromtimestamp(start_time, tz=pytz.UTC)
        end_time = datetime.datetime.fromtimestamp(end_time, tz=pytz.UTC)
        Mdata_IB = pd.DataFrame(columns=self.columns)
        for metric_name in self.IB_traffic.keys():
            query_result = self.prom.custom_query_range(
                self.IB_traffic[metric_name],
                end_time=end_time,
                start_time=start_time,
                step="1s",
            )
            for m in query_result:
                data_t = pd.json_normalize(m)
                dk_tmp = pd.DataFrame(columns=self.columns)

                for i, r in data_t.iterrows():

                    df_values = pd.DataFrame(
                        r["values"], columns=["timestamp", "value"]
                    )
                    df_values["instance"] = r["metric.kubernetes_node"]

                    dk_tmp = pd.concat([dk_tmp, df_values], axis=0)
                dk_tmp["metrics"] = metric_name
                Mdata_IB = pd.concat([Mdata_IB, dk_tmp], axis=0)
            print(f"IB : {metric_name}")
        return Mdata_IB

    def collect_pod_metrics(self, start_time, end_time):
        start_time = datetime.datetime.fromtimestamp(start_time, tz=pytz.UTC)
        end_time = datetime.datetime.fromtimestamp(end_time, tz=pytz.UTC)
        Mdata_pod = pd.DataFrame(columns=self.columns_pod)
        for metric_name in self.mdict_pod.keys():
            query_result = self.prom.custom_query_range(
                self.mdict_pod[metric_name],
                end_time=end_time,
                start_time=start_time,
                step="5s",
            )
            for m in query_result:
                data_t = pd.json_normalize(m)
                dk_tmp = pd.DataFrame(columns=self.columns_pod)
                for i, r in data_t.iterrows():
                    df_values = pd.DataFrame(
                        r["values"], columns=["timestamp", "value"]
                    )
                    df_values["pod"] = r["metric.pod"]
                    dk_tmp = pd.concat([dk_tmp, df_values], axis=0)
                dk_tmp["metrics"] = metric_name
                Mdata_pod = pd.concat([Mdata_pod, dk_tmp], axis=0)
            print(f"pod : {metric_name}")
        return Mdata_pod

    def collect_deploy_metrics(self, start_time, end_time):
        start_time = datetime.datetime.fromtimestamp(start_time, tz=pytz.UTC)
        end_time = datetime.datetime.fromtimestamp(end_time, tz=pytz.UTC)
        Mdata_deployment = pd.DataFrame(columns=self.columns_pod)
        for metric_name in self.deploy_metrics.keys():
            query_result = self.prom.custom_query_range(
                self.deploy_metrics[metric_name],
                end_time=end_time,
                start_time=start_time,
                step="5s",
            )
            for m in query_result:
                data_t = pd.json_normalize(m)
                dk_tmp = pd.DataFrame(columns=self.columns_pod)
                for i, r in data_t.iterrows():
                    df_values = pd.DataFrame(
                        r["values"], columns=["timestamp", "value"]
                    )
                    df_values["deployment"] = r["metric.deployment"]
                    dk_tmp = pd.concat([dk_tmp, df_values], axis=0)
                dk_tmp["metrics"] = metric_name
                Mdata_deployment = pd.concat([Mdata_deployment, dk_tmp], axis=0)
            print(f"deployment : {metric_name}")
        return Mdata_deployment

    def collect_sum_metrics(self, start_time, end_time):
        start_time = datetime.datetime.fromtimestamp(start_time, tz=pytz.UTC)
        end_time = datetime.datetime.fromtimestamp(end_time, tz=pytz.UTC)
        Mdata_sum = pd.DataFrame()
        for metric_name in self.sum_metrics.keys():
            query_result = self.prom.custom_query_range(
                self.sum_metrics[metric_name],
                end_time=end_time,
                start_time=start_time,
                step="5s",
            )
            for m in query_result:
                data_t = pd.json_normalize(m)
                dk_tmp = pd.DataFrame()
                for i, r in data_t.iterrows():
                    df_values = pd.DataFrame(
                        r["values"], columns=["timestamp", "value"]
                    )
                    # df_values['deployment'] = r['metric.deployment']
                    dk_tmp = pd.concat([dk_tmp, df_values], axis=0)
                dk_tmp["metrics"] = metric_name
                Mdata_sum = pd.concat([Mdata_sum, dk_tmp], axis=0)
            print(f"sum : {metric_name}")
        return Mdata_sum

    def get_node_metrics(self, start_time, end_time):
        start_time = datetime.datetime.fromtimestamp(start_time, tz=pytz.UTC)
        end_time = datetime.datetime.fromtimestamp(end_time, tz=pytz.UTC)
        Mdata_node = pd.DataFrame(columns=self.columns)
        Mdata_GPU = pd.DataFrame(columns=self.columns)
        for metric_name in self.mdict_node_hybird.keys():
            query_result = self.prom.custom_query_range(
                self.mdict_node_hybird[metric_name],
                end_time=end_time,
                start_time=start_time,
                step="5s",
            )
            if "gpu" in metric_name:
                for m in query_result:
                    data_t = pd.json_normalize(m)
                    dk_tmp = pd.DataFrame(columns=self.columns_gpu)
                    for i, r in data_t.iterrows():
                        df_values = pd.DataFrame(
                            r["values"], columns=["timestamp", "value"]
                        )
                        df_values["gpu"] = r["metric.gpu"]
                        df_values["modelName"] = r["metric.modelName"]
                        df_values["instance"] = r["metric.kubernetes_node"]
                        dk_tmp = pd.concat([dk_tmp, df_values], axis=0)
                    dk_tmp["metrics"] = metric_name
                    Mdata_GPU = pd.concat([Mdata_GPU, dk_tmp], axis=0)
            else:
                for m in query_result:
                    data_t = pd.json_normalize(m)
                    dk_tmp = pd.DataFrame(columns=self.columns)
                    for i, r in data_t.iterrows():
                        df_values = pd.DataFrame(
                            r["values"], columns=["timestamp", "value"]
                        )
                        dk_tmp = pd.concat([dk_tmp, df_values], axis=0)
                    dk_tmp["metrics"] = metric_name
                    dk_tmp["ip"] = m["metric"]["instance"].split(":")[0]
                    Mdata_node = pd.concat([Mdata_node, dk_tmp], axis=0)
                    Mdata_node["instance"] = Mdata_node["ip"].apply(
                        lambda x: node_ip[x]
                    )
            print(f"node : {metric_name}")
        return Mdata_node, Mdata_GPU

    def collect_all_metrics(self, start_time, end_time):
        node_perf, gpu_perf = self.get_node_metrics(start_time, end_time)
        return node_perf, gpu_perf


class OpenFaasCollector:
    def __init__(self, fun_name):
        self.prom = PrometheusConnect(
            url="http://prometheus.example/", disable_ssl=False
        )
        # irate(gateway_functions_seconds_sum{function_name=~'bert-21b-submod-.*-latency-10.openfaas-fn'}[60s])
        self.sum_metrics = {
            # 'latency':f'avg by (function_name, code)(rate(gateway_functions_seconds_sum[30s]) / rate(gateway_functions_seconds_count[30s]))',
            "execution_time": f'(irate(gateway_functions_seconds_sum{{function_name=~"{fun_name}"}}[60s]) / irate(gateway_functions_seconds_count{{function_name=~"{fun_name}"}}[60s]))',
            "response": f'irate(gateway_function_invocation_total{{function_name=~"{fun_name}"}}[60s])',
            "latency": f'avg by (function_name, code)(rate(gateway_functions_seconds_sum{{function_name=~"{fun_name}"}}[30s]) / rate(gateway_functions_seconds_count{{function_name=~"{fun_name}"}}[30s]))',
            "scale": f'sum(gateway_service_count{{function_name=~"{fun_name}"}}) by (function_name)/2',
            "rps": f'sum(irate(gateway_function_invocation_total{{function_name=~"{fun_name}"}}[60s])) by (function_name)',
            "queue": f'sum by (function_name) (gateway_function_invocation_started{{function_name=~"{fun_name}"}}) - sum by (function_name) (gateway_function_invocation_total{{function_name=~"{fun_name}"}})',
            "invalid": f'sum by (function_name)(irate(gateway_function_invocation_total{{function_name=~"{fun_name}",code!="200"}}[60s]))',
            "goodput": f'sum by (function_name)(irate(gateway_function_invocation_total{{function_name=~"{fun_name}",code="200"}}[60s]))',
        }
        self.invoke_count_metrics = {
            "invoke_count": f'sum by (function_name) (gateway_function_invocation_total{{function_name=~"{fun_name}"}})',
        }

        print(f"openfaas : {fun_name}")

    def collect_invoke_count(self, start_time, end_time):
        dk = pd.DataFrame(
            columns=["timestamp", "value", "function", "metrics", "time_type"]
        )

        for time_type, query_time in zip(["start", "end"], [start_time, end_time]):
            for metric_name in self.invoke_count_metrics.keys():
                query_result = self.prom.custom_query(
                    self.invoke_count_metrics[metric_name],
                    params={"time": query_time},
                )

                for m in query_result:
                    timestamp = m["value"][0]
                    value = m["value"][1]
                    function_name = m["metric"]["function_name"]

                    df_values = pd.DataFrame(
                        {
                            "timestamp": [timestamp],
                            "value": [value],
                            "function": [function_name],
                            "metrics": [metric_name],
                            "time_type": [time_type],
                        }
                    )

                    dk = pd.concat([dk, df_values], axis=0, ignore_index=True)
                    print(f"openfaas invoke count : {function_name} {metric_name}")
        return dk

    def collect_sum_metrics(self, start_time, end_time):
        start_time = datetime.datetime.fromtimestamp(start_time, tz=pytz.UTC)
        end_time = datetime.datetime.fromtimestamp(end_time, tz=pytz.UTC)
        dk = pd.DataFrame()
        for metric_name in self.sum_metrics.keys():
            # print(self.sum_metrics[metric_name])
            query_result = self.prom.custom_query_range(
                self.sum_metrics[metric_name],
                end_time=end_time,
                start_time=start_time,
                step="5s",
            )
            for m in query_result:
                data_t = pd.json_normalize(m)
                for i, r in data_t.iterrows():
                    df_values = pd.DataFrame(
                        r["values"], columns=["timestamp", "value"]
                    )
                    df_values["function"] = r["metric.function_name"]
                    df_values["metrics"] = metric_name
                    dk = pd.concat([dk, df_values], axis=0)
            print(f"openfaas : {fun_name} {metric_name}")
        return dk


def get_csv_files_in_bottom_directory(root_path):
    csv_files = []
    for root, dirs, files in os.walk(root_path):
        if not dirs:
            csv_files.extend(glob.glob(os.path.join(root, "*.csv")))
    return csv_files


if __name__ == "__main__":
    # uuid,wrkname,scaling,scheduler,start_time,end_time,slo,cv,collected,csv
    # df2d68a1-2ace-4f52-b06e-67d5aefcd82d,shuffler,none,ours,1746389147,1746390008,10,0.1,0,../metrics/wula/ES1_25/shuffler/ours/1746389147/
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
    exp = "ES1"
    record_csv = f"./evaluation_record/record_{exp}.csv"
    record = pd.read_csv(record_csv, header=0)
    interval = 1
    kubernetes_node_ES1 = ""
    mc = MetricCollector(kubernetes_node_ES1)

    # kubernetes_node_hybird3 =
    for i, r in record.iterrows():
        start_time_rc = r["start_time"]
        # end time = start_time_rc +11min
        end_time_rc = start_time_rc + 12 * 60
        scheduler = r["scheduler"]
        cv = r["cv"]
        csv = r["csv"]
        print(
            f'collecting {exp} {r["wrkname"]}, {r["start_time"]}, {r["end_time"]}, total time: {(r["end_time"]-r["start_time"])/3600},csv path: {csv}',
        )
        evaluation_id = r["uuid"]
        wrkname = r["wrkname"]
        # if wrkname!='inferline':
        #     continue
        
        collect_record = pd.DataFrame(columns=["uuid", "cv", "collected"])
            
        for csv_file in get_csv_files_in_bottom_directory(csv):
            file_name = os.path.basename(csv_file)
            cv_mu_duration = csv_file.split("/")[-2]                
            prom_path = f"../system/{exp}/{scheduler}/{cv}/{start_time_rc}/"
            
            df = pd.read_csv(csv_file)           
            df["RequestTime"] = df["RequestTime"].astype(int)
            df["ResponseTime"] = df["ResponseTime"].astype(int)
            max_request_time = df["RequestTime"].max()
            min_request_time = df["RequestTime"].min()
            max_response_time = df["ResponseTime"].max()
            min_response_time = df["ResponseTime"].min()

            start_time_r = min(min_request_time, min_response_time) / 1e9
            end_time_r = max(max_request_time, max_response_time) / 1e9

            start_time = start_time_r - interval
            end_time = end_time_r + interval
            
            if not os.path.exists(prom_path):
                os.makedirs(prom_path)
            print(f"prom_path : {prom_path}")
            print(collect_record)
            if (
                not collect_record[
                    (collect_record["uuid"] == evaluation_id) & (collect_record["cv"] == cv)
                ]["collected"]
                .eq(1)
                .any()
            ):
                IB_df = mc.collect_node_IB_metrics(
                    start_time=start_time_rc, end_time=end_time_rc
                )
                IB_df.to_csv(
                    f"{prom_path}/{start_time_rc}_IB.csv",
                    header=False,
                )
                cpu_context_switches_df = mc.collect_node_cpu_context_switches_metrics(
                    start_time=start_time_rc, end_time=end_time_rc
                )
                cpu_context_switches_df.to_csv(
                    f"{prom_path}/{start_time_rc}_cpu.csv",
                    header=False,
                )
                node_pd, gpu_pd = mc.get_node_metrics(start_time_rc, end_time_rc)
                gpu_pd.to_csv(
                    f"{prom_path}/{start_time_rc}_gpu.csv",
                    header=False,
                )

                pod_pd = mc.collect_pod_metrics(start_time_rc, end_time_rc)
                pod_pd.to_csv(
                    f"{prom_path}/{start_time_rc}_pod.csv",
                    header=False,
                )
                sum_pd = mc.collect_sum_metrics(start_time_rc, end_time_rc)
                sum_pd.to_csv(
                    f"{prom_path}/{start_time_rc}_sum.csv",
                    header=False,
                )
                collect_record = collect_record._append(
                    {
                        "uuid": evaluation_id,
                        "cv": cv,
                        "collected": 1,
                    },
                    ignore_index=True,
                )
            openfaas_prom_path = f"../metrics/openfaas/{exp}/{scheduler}/{cv}/{start_time_rc}/"
            os.makedirs(openfaas_prom_path, exist_ok=True)
            fun_url = df["URL"].unique().tolist()
            fun_name_list = [
                re.sub(r"-(\d+)-", r"-.*-", s.split("/")[-1]) for s in fun_url
            ]
            fun_name_list = list(set(fun_name_list))
            for fun_name in fun_name_list:
                openfaas_prom = OpenFaasCollector(fun_name)
                scale_df = openfaas_prom.collect_sum_metrics(
                    start_time=start_time, end_time=end_time
                )
                scale_df["evaluation_id"] = evaluation_id
                # scale_df["cv_mu_duration"] = cv_mu_duration
                scale_df.to_csv(
                    f"{openfaas_prom_path}/openfaas_{evaluation_id}.csv",
                    header=False,
                    mode="a",
                )
                invoke_df = openfaas_prom.collect_invoke_count(
                    start_time=start_time, end_time=end_time
                )
                invoke_df["evaluation_id"] = evaluation_id
                # invoke_df["cv_mu_duration"] = cv_mu_duration
                invoke_df.to_csv(
                    f"{openfaas_prom_path}/invoke_{evaluation_id}.csv",
                    header=False,
                    mode="a",
                )

        record.loc[i, "collected"] = 1
        record.to_csv(record_csv, header=True, index=False)
