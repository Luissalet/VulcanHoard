"""Observed Ollama and GPU state; model operations remain asynchronous."""

import csv, io, json, re, subprocess, threading, time
import httpx
from .vision import OLLAMA

operation = {"status": "idle"}
_cache = {}
_lock = threading.RLock()


def operation_error(error):
    message = str(error)
    if any(
        s in message.lower()
        for s in ["certificate", "unknown authority", "self signed", "self-signed"]
    ):
        return "La descarga no pudo verificar el certificado del servidor. No se ha desactivado la verificación; puedes reintentar o usar otro origen de confianza."
    if "not found" in message.lower() or "status code: 404" in message.lower():
        return "No se encontró ese modelo. Comprueba el nombre y la variante."
    # Signed download URLs can be both sensitive and much longer than the error.
    return re.sub(r"https?://[^\s\"<>]+", "[servidor del modelo]", message)[:500]


def snapshot():
    with _lock:
        if time.time() - _cache.get("sampledAt", 0) < 3:
            return _cache | {"operation": dict(operation)}
        data = {
            "sampledAt": time.time(),
            "online": False,
            "loaded": [],
            "gpus": [],
            "gpuError": None,
        }
        try:
            with httpx.Client(timeout=3, trust_env=False) as client:
                r = client.get(OLLAMA + "/api/ps")
                r.raise_for_status()
                data.update(online=True, loaded=r.json().get("models", []))
        except Exception as e:
            data["ollamaError"] = str(e)[:200]
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=True,
            )

            def number(value):
                try:
                    return float(value.strip())
                except ValueError:
                    return None

            for row in csv.reader(io.StringIO(result.stdout)):
                if len(row) != 6:
                    continue
                data["gpus"].append(
                    dict(
                        zip(
                            ["index", "name", "totalMiB", "usedMiB", "utilization", "temperature"],
                            [row[0].strip(), row[1].strip(), *[number(x) for x in row[2:]]],
                        )
                    )
                )
        except Exception as e:
            data["gpuError"] = "No se pudo leer la GPU: " + str(e)[:180]
        _cache.clear()
        _cache.update(data)
        return data | {"operation": dict(operation)}


def busy():
    return operation.get("status") == "running"


def start(action, model):
    if busy():
        raise ValueError("Ya hay una operación de modelo en curso.")
    operation.clear()
    operation.update(
        status="running",
        action=action,
        model=model,
        started=time.time(),
        message="Preparando modelo…",
    )

    def work():
        try:
            with httpx.Client(timeout=httpx.Timeout(900, connect=8), trust_env=False) as client:
                if action == "download":
                    with client.stream(
                        "POST", OLLAMA + "/api/pull", json={"model": model, "stream": True}
                    ) as response:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if not line:
                                continue
                            state = json.loads(line)
                            if state.get("error"):
                                raise ValueError(state["error"])
                            status = state.get("status", "")
                            message = {
                                "pulling manifest": "Comprobando disponibilidad del modelo…",
                                "verifying sha256 digest": "Verificando la descarga…",
                                "writing manifest": "Registrando el modelo…",
                                "success": "Descarga completada.",
                            }.get(status, "Descargando archivos del modelo…")
                            operation.update(
                                message=message,
                                completed=state.get("completed"),
                                total=state.get("total"),
                            )
                else:
                    response = client.post(
                        OLLAMA + "/api/generate",
                        json={
                            "model": model,
                            "stream": False,
                            "keep_alive": 0 if action == "unload" else "30m",
                            "options": {"num_ctx": 16384},
                        },
                    )
                    response.raise_for_status()
                    if response.json().get("error"):
                        raise ValueError(response.json()["error"])
                check = client.get(OLLAMA + "/api/ps")
                check.raise_for_status()
                loaded = {m["name"] for m in check.json().get("models", [])}
                found = model in loaded or model + ":latest" in loaded
                if action == "load" and not found:
                    raise ValueError("Ollama respondió, pero el modelo no figura como cargado.")
                if action == "unload" and found:
                    raise ValueError(
                        "El modelo sigue en memoria; otra aplicación podría estar utilizándolo."
                    )
            operation.update(
                status="done",
                finished=time.time(),
                message={
                    "load": "Modelo cargado.",
                    "unload": "Modelo liberado de memoria.",
                    "download": "Modelo descargado al disco.",
                }[action],
            )
        except Exception as e:
            operation.update(status="error", finished=time.time(), message=operation_error(e))
        _cache.clear()

    threading.Thread(target=work, daemon=True, name="model-operation").start()
    return dict(operation)
