"""Direct ASGI dispatch: keep SSE, range requests and large files streaming."""
import asyncio
import json
import re
from .cancellation import join_tasks

from starlette.responses import JSONResponse, Response


class NativeResponse(Response):
    def __init__(self, module, path, *, name, product, on_quit):
        super().__init__()
        self.module, self.path = module, path
        self.name, self.product, self.on_quit = name, product, on_quit

    def decorate(self, body, headers):
        key, path, product, name = self.module.spec["id"], self.path, self.product, self.name
        ctype = headers.get("content-type", "")
        if "text/html" in ctype:
            # Native source remains owned by Vulcan. Scope its browser network
            # and assets while preserving its complete editor and hash router.
            body = re.sub(rb'((?:src|href|action)=[\x22\x27])/(?!/|fusion-static/)([^\x22\x27]*)',
                          lambda match: match[1] + f'/{key}/'.encode() + match[2], body)
            bootstrap = f'<base href="/{key}/"><script src="/fusion-static/native-scope.js" data-studio-module="{key}"></script>'.encode()
            body = re.sub(rb'(<head[^>]*>)', lambda match: match[0] + bootstrap, body, count=1)
            body = re.sub(rb'<html\b(?![^>]*data-hoard-app)', f'<html data-hoard-app="{product}"'.encode(), body, count=1)
            body = re.sub(rb'<title>.*?</title>', lambda m: ("<title>" + name + " · " + self.module.spec["name"] + "</title>").encode(), body, count=1)
            body = re.sub(rb'data-hoard-app="[^"]*"', f'data-hoard-app="{product}"'.encode(), body)
            extra = b'<link rel="stylesheet" href="/fusion-static/hoard-theme.css"><link rel="stylesheet" href="/fusion-static/product-theme.css"><link rel="stylesheet" href="/fusion-static/module-shell.css">'
            body = body.replace(b"</head>", extra + b"</head>", 1)
            bar = (f'<nav class="fusion-bar" aria-label="{name}"><a href="/"><img src="/fusion-static/icon-192.png" width="28" height="28" alt="">{name}</a>'
                   f'<span>{self.module.spec["name"]}</span><a class="fusion-functions" href="/">Funciones</a></nav>').encode()
            body = re.sub(rb'(<body[^>]*>)', lambda m: m[0] + bar, body, count=1)
            body = body.replace(b'"/api/copilot/', f'"/{key}/api/copilot/'.encode()).replace(b"'/api/copilot/", f"'/{key}/api/copilot/".encode())
        if 'javascript' in ctype or 'text/css' in ctype:
            body = re.sub(rb'([\x22\x27\x60(])/(assets|static|fonts)/',
                          lambda match: match[1] + f'/{key}/'.encode() + match[2] + b'/', body)
        if path == "manifest.webmanifest" and "json" in ctype:
            manifest = json.loads(body)
            manifest.update(name=name + " · " + self.module.spec["name"], short_name=name.split("'")[0],
                            start_url=f"/{key}/", scope=f"/{key}/")
            manifest["icons"] = [{"src": "/fusion-static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                                 {"src": "/fusion-static/icon-512.png", "sizes": "512x512", "type": "image/png"}]
            body = json.dumps(manifest).encode()
        if path == "sw.js":
            body = body.replace(b'const CACHE_NAME = ', f'const CACHE_NAME = "{key}-" + '.encode())
            body = body.replace(b'"/api/"', f'"/{key}/api/"'.encode()).replace(b'"/assets/"', f'"/{key}/assets/"'.encode())
            body = body.replace(b'name !== CACHE_NAME', f'name.startsWith("{key}-") && name !== CACHE_NAME'.encode())
            headers["service-worker-allowed"] = f"/{key}/"
        return body

    async def __call__(self, scope, receive, send):
        module = self.module
        task = asyncio.current_task()
        async with module.lock:
            if module.app is None or module.stopping:
                return await JSONResponse({"error": "module_disabled", "module": module.spec["id"]}, status_code=409)(scope, receive, send)
            native = module.app
        child_scope = dict(scope, path="/" + self.path, raw_path=("/" + self.path).encode(), root_path="")
        headers = [(k, v) for k, v in scope["headers"] if k not in (b"authorization", b"accept-encoding")]
        token_path = module.data_dir / "mcp-token"
        if token_path.exists():
            headers.append((b"authorization", b"Bearer " + token_path.read_text(encoding="utf-8").strip().encode()))
        child_scope["headers"] = headers
        start, chunks, buffered = None, [], False

        async def forward(message):
            nonlocal start, buffered
            if message["type"] == "http.response.start":
                start = dict(message)
                hdr = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
                ctype = hdr.get("content-type", "")
                buffered = "text/html" in ctype or "javascript" in ctype or "text/css" in ctype or self.path in ("manifest.webmanifest", "sw.js")
                if "text/event-stream" in ctype:
                    module.requests[task] = True
                if hdr.get("location", "").startswith("/"):
                    start["headers"] = [(k, ("/" + module.spec["id"] + v.decode()).encode() if k.lower() == b"location" else v) for k, v in message["headers"]]
                if not buffered:
                    await send(start)
                return
            if message["type"] == "http.response.body" and buffered:
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    hdr = {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}
                    body = self.decorate(b"".join(chunks), hdr)
                    hdr.pop("content-length", None)
                    hdr["content-length"] = str(len(body))
                    start["headers"] = [(k.encode(), v.encode()) for k, v in hdr.items()]
                    await send(start)
                    await send({"type": "http.response.body", "body": body})
                return
            await send(message)

        try:
            # Own the native request independently from the outer HTTP scope.
            # Concurrent disconnect/OFF cannot cancel its cleanup twice.
            task = asyncio.create_task(native(child_scope, receive, forward))
            module.requests[task] = self.path.endswith('/live')
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await join_tasks([task], cancel=True)
                raise
        finally:
            module.requests.pop(task, None)
