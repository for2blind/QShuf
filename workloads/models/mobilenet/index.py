#!/usr/bin/env python
import os, re
import time
import subprocess
import gc
import torch
import functools
import asyncio
import logging
from threading import Thread
import uvicorn
from fastapi.responses import JSONResponse
from fastapi import FastAPI, Request, HTTPException
from contextlib import asynccontextmanager

from collections import deque
recent_exec_times = deque(maxlen=3)

print("Starting server")
def get_cuda_device_mapping():
    try:
        output = subprocess.check_output(["nvidia-smi", "-L"], universal_newlines=True)
    except subprocess.CalledProcessError:
        print(
            "nvidia-smi could not be executed. Are you sure the system has an NVIDIA GPU and nvidia-smi is installed?"
        )
        return {}
    except FileNotFoundError:
        print("nvidia-smi was not found. Are you sure it's installed?")
        return {}
    devices = re.findall(r"GPU (\d+): (.* \(UUID: GPU-(.*)\))", output)
    uuid_to_id = {uuid: int(id) for id, _, uuid in devices}
    return uuid_to_id


def get_device_id_by_uuid(target_uuid):
    uuid_to_id = get_cuda_device_mapping()
    return uuid_to_id.get(target_uuid, None)


retry_check = os.environ.get("retry_check", "false").lower() == "true"
global infer_device
if os.environ.get("infer_device") is None:
    infer_device = "cuda" if torch.cuda.is_available() else "cpu"
    os.environ["infer_device"] = infer_device
elif os.environ["infer_device"] == "cuda" and not torch.cuda.is_available():
    os.environ["infer_device"] = "cpu"
infer_device = os.environ.get("infer_device")

function_time_out = float(os.environ.get("read_timeout", "10s").replace("s", ""))
# function_time_out = 10
model_name = os.getenv("infer_model_name")
class LoadedApp:
    def __init__(self):
        self.app = FastAPI()
        self.loaded = False
        self.ready = False
        self.setup_routes()

    def setup_routes(self):
        @self.app.get("/tocpu")
        async def tocpu():
            start_time = time.time()
            global infer_device,model,x
            with torch.inference_mode():
                infer_device = "cpu"
                x = x.to("cpu")
                model = model.to("cpu")
                # print(model(x))
                torch.cuda.empty_cache()
            self.ready = True
            end_time = time.time()
            return JSONResponse(
                content={"result": f"Model moved to CPU in {end_time-start_time}s"},
                status_code=200,
            )
        
        @self.app.get("/tocuda")
        async def tocuda():
            if not torch.cuda.is_available():
                return JSONResponse(
                    content={"result": "CUDA is not available"}, status_code=500
                )
            start_time = time.time()
            # self.ready = False
            global infer_device,model,x
            with torch.inference_mode():
                infer_device = 'cuda'
                model = model.to('cuda')
                x = x.to('cuda')
                torch.cuda.empty_cache()
            end_time = time.time()
            # self.loaded = True  # set loaded to True since it's ready to serving on cuda
            self.ready = True
            return JSONResponse(
                content={"result": f"Model moved to CUDA in {end_time-start_time}s"},
                status_code=200,
            )
            
        @self.app.get("/tonotready")
        async def notready():
            self.ready = False
            return JSONResponse(
                content={"result": "Model not ready"}, status_code=200
            )
        
        @self.app.get("/toready")
        async def ready():
            if self.loaded:
                self.ready = True
                return JSONResponse(
                    content={"result": "Model ready"}, status_code=200
                )
            else:
                self.ready = False
                return JSONResponse(
                    content={"result": "Model not ready due to not loaded"}, status_code=500
                )

        @self.app.get("/loaded")
        async def check_loaded():
            return JSONResponse(
                content={"result": self.ready}, status_code=200 if self.ready else 500
            )

        @self.app.post("/set_env")
        async def set_env(request: Request):
            try:
                data = await request.json()
                for key, value in data.items():
                    os.environ[key] = value
                return JSONResponse(
                    content={"message": "Environment variables updated successfully."},
                    status_code=200,
                )
            except Exception as e:
                return JSONResponse(content={"error": str(e)}, status_code=500)

        @self.app.get("/get_env")
        async def get_env(request: Request):
            try:
                variable_name = request.query_params.get("name")
                if variable_name:
                    if variable_name in os.environ:
                        return JSONResponse(
                            content={variable_name: os.environ[variable_name]},
                            status_code=200,
                        )
                    else:
                        raise HTTPException(
                            status_code=404,
                            detail=f"Environment variable '{variable_name}' not found.",
                        )
                else:
                    env_vars = {key: os.environ[key] for key in os.environ}
                    return JSONResponse(
                        content={"environment": env_vars}, status_code=200
                    )
            except Exception as e:
                return JSONResponse(content={"error": str(e)}, status_code=500)

    async def serve(self):
        class CustomAccessLogFilter(logging.Filter):
            def filter(self, record):
                return '/loaded' not in record.getMessage()
        logging.getLogger("uvicorn.access").addFilter(CustomAccessLogFilter())
        config = uvicorn.Config(self.app, host="0.0.0.0", port=5001)
        server = uvicorn.Server(config)
        await server.serve()

def load_model_and_update_status(loaded_app):
    start_time = time.time()
    with torch.inference_mode():
        global infer_device,model, x
        print("infer_device = ",infer_device)
        x = torch.randn(32, 3, 32, 32).to(infer_device)
        model= torch.load(f'/sharing/model/mobilenet_v11_3.pt',map_location=infer_device)
        print("model loaded on",infer_device)
        output = model(x)
    loaded_app.loaded = True
    if os.environ.get("reserve",default='False') == 'True':
        print("reserve pod")
        loaded_app.ready = False
    else:
        print("not reserve pod")
        loaded_app.ready = True
    print("loaded", "time:", time.time() - start_time)

inference_semaphore = asyncio.Semaphore(1)
if infer_device != "cpu":
    s = torch.cuda.Stream("cuda")
async def perform_inference():
    with torch.inference_mode():
        async with inference_semaphore:
            if infer_device != "cpu":
                if s is not None:  
                    with torch.cuda.stream(s):
                        output = model(x)
                        s.synchronize()
                else:
                    output = model(x)
                    torch.cuda.synchronize()
            else:
                output = model(x)
            gc.collect()
            torch.cuda.empty_cache()
            return output

worker_stop_event = asyncio.Event()
inference_queue = asyncio.Queue()
TIMEOUT_FACTOR = float(os.environ.get("TIMEOUT_FACTOR", "1.5"))
async def inference_worker():
    try:
        while not worker_stop_event.is_set() or not inference_queue.empty():
            try:
                future = await asyncio.wait_for(inference_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if future.cancelled():
                inference_queue.task_done()
                continue
            put_time = getattr(future, "put_time", None)
            if put_time is None:
                inference_queue.task_done()
                continue
            waited = time.time() - put_time
            remaining_time = function_time_out - waited
            if len(recent_exec_times) >= 3:
                avg_exec_time = sum(recent_exec_times) / len(recent_exec_times)
                if remaining_time < avg_exec_time * TIMEOUT_FACTOR:
                    future.cancel()
                    inference_queue.task_done()
                    print("Predicted timeout: task cancelled before execution.")
                    continue
            try:
                output = await perform_inference()
                exec_time = time.time() - put_time
                recent_exec_times.append(exec_time)
                future.set_result(output)
            except Exception as e:
                future.set_exception(e)
            finally:
                inference_queue.task_done()
    except asyncio.CancelledError:
        pass


async def listen_for_disconnect(request: Request, handler_task: asyncio.Task) -> None:
    """Returns if a disconnect message is received"""
    while True:
        message = await request.receive()
        # print("message = ", message)
        if message["type"] == "http.disconnect":
            # print("Client disconnected")
            handler_task.cancel()
            return handler_task


def with_cancellation(handler_func):
    # Functools.wraps is required for this wrapper to appear to fastapi as a
    # normal route handler, with the correct request type hinting.
    @functools.wraps(handler_func)
    async def wrapper(*args, **kwargs):
        # The request is either the second positional arg or `raw_request`
        # print("args = ",args) 
        # request = args[1] if len(args) > 1 else kwargs["raw_request"]
        request: Request = kwargs.get("request") or args[-1]
        handler_task = asyncio.create_task(handler_func(*args, **kwargs))
        cancellation_task = asyncio.create_task(listen_for_disconnect(request, handler_task))
        done, pending = await asyncio.wait([handler_task, cancellation_task],
                                           return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if handler_task in done:
            return handler_task.result()
        return None
    return wrapper

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(inference_worker())
    yield
    print("[Lifespan] Shutdown: Clearing inference queue...")
    worker_stop_event.set()
    while not inference_queue.empty():
        try:
            future = inference_queue.get_nowait()
            if not future.done():
                future.cancel()
            inference_queue.task_done()
        except asyncio.QueueEmpty:
            break
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    print("[Lifespan] Inference worker stopped and queue cleared.")

app = FastAPI(lifespan=lifespan)
    

@app.api_route("/{path:path}", methods=["GET", "PUT", "POST", "PATCH", "DELETE"])
@with_cancellation
async def call_handler(path: str, request: Request):
    start_time = time.time()
    try:
        future = asyncio.get_event_loop().create_future()
        future.put_time = start_time
        await inference_queue.put(future)
        # print("Inference task added to queue")
        done, pending = await asyncio.wait(
            [future],
            timeout=function_time_out,
            return_when=asyncio.FIRST_COMPLETED
        )
        if future not in done:
            future.cancel()
            print(f"Inference task exceeded timeout of {function_time_out} seconds.")
            raise HTTPException(status_code=408, detail="Inference task timeout")
        output = await future
        # print("Inference result:", output)
        end_time = time.time()
        res = {
            "start_time": start_time,
            "end_time": end_time,
            "infer_device": infer_device,
            "exec_time": end_time - start_time,
        }
        return str(res), 200
    except asyncio.CancelledError:
        if not future.done():
            future.cancel()
        print("Inference cancelled due to client disconnect.")
        raise HTTPException(status_code=499, detail="Client disconnected during inference.")
    except Exception as e:
        print(e)
        error_response = {
            "result": f"inference except: {e}",
            "end_time": time.time(),
            "t_exec": time.time() - start_time,
            "stage": 1,
        }
        raise HTTPException(status_code=500, detail=error_response)

if __name__ == "__main__":
    loaded_app = LoadedApp()
    app_thread = Thread(target=asyncio.run, args=(loaded_app.serve(),))
    app_thread.daemon = True
    app_thread.start()
    load_model_and_update_status(loaded_app)
    loop = asyncio.get_event_loop()
    main_config = uvicorn.Config(
        app, host="0.0.0.0", port=5000, timeout_keep_alive=function_time_out
    )
    main_server = uvicorn.Server(main_config)
    loop.run_until_complete(main_server.serve())
