# scripts/ui_check/cdp.py — a tiny Chrome DevTools driver: launch headless Chrome, open pages, click/type/wait, screenshot,
# collect JS errors. Node is not installed here, so this is how the extension's JavaScript is exercised for real.
import asyncio, base64, json, os, shutil, subprocess, tempfile, time
import aiohttp

CHROME = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PORT = 9333
STUB = """
window.chrome = {
  storage: { local: { get: async () => ({}), set: async () => {}, remove: async () => {} }, onChanged: { addListener() {} } },
  runtime: { sendMessage: async () => ({}), onMessage: { addListener() {} }, getURL: (p) => p },
  tabs: { create() {} },
};
"""

class Browser:
    async def start(self):
        self.dir = tempfile.mkdtemp(prefix="ui-chrome-")
        self.proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={self.dir}",
                                      "--no-first-run", "--disable-gpu", "--hide-scrollbars", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.http = aiohttp.ClientSession()
        for _ in range(60):
            try:
                async with self.http.get(f"http://127.0.0.1:{PORT}/json/version") as r:
                    if r.status == 200: return self
            except Exception: await asyncio.sleep(0.25)
        raise RuntimeError("chrome did not start")

    async def page(self, url, width=420, height=900):
        async with self.http.put(f"http://127.0.0.1:{PORT}/json/new?about:blank") as r:
            target = await r.json()
        p = Page(self, target); await p.open(width, height); await p.goto(url); return p

    async def stop(self):
        await self.http.close(); self.proc.terminate()
        try: self.proc.wait(10)
        except subprocess.TimeoutExpired: self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)

class Page:
    def __init__(self, browser, target):
        self.b, self.target, self.id, self.errors, self.logs = browser, target, 0, [], []
        self.pending = {}

    async def open(self, width, height):
        self.ws = await self.b.http.ws_connect(self.target["webSocketDebuggerUrl"], max_msg_size=0)
        self.reader = asyncio.create_task(self._read())
        for m in ("Page.enable", "Runtime.enable", "Log.enable"): await self.send(m)
        await self.send("Page.addScriptToEvaluateOnNewDocument", {"source": STUB})
        await self.send("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
        await self.send("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": "dark"}]})

    async def _read(self):
        async for msg in self.ws:
            d = json.loads(msg.data)
            if "id" in d and d["id"] in self.pending: self.pending.pop(d["id"]).set_result(d); continue
            m, p = d.get("method"), d.get("params", {})
            if m == "Runtime.exceptionThrown": self.errors.append(p["exceptionDetails"].get("exception", {}).get("description") or p["exceptionDetails"]["text"])
            elif m == "Runtime.consoleAPICalled" and p["type"] in ("error", "warning"): self.logs.append(f'{p["type"]}: ' + " ".join(str(a.get("value", a.get("description", ""))) for a in p["args"]))
            elif m == "Log.entryAdded" and p["entry"]["level"] == "error": self.logs.append("log: " + p["entry"]["text"] + " " + p["entry"].get("url", ""))

    async def send(self, method, params=None):
        self.id += 1; fut = asyncio.get_event_loop().create_future(); self.pending[self.id] = fut
        await self.ws.send_str(json.dumps({"id": self.id, "method": method, "params": params or {}}))
        d = await fut
        if "error" in d: raise RuntimeError(f"{method}: {d['error']}")
        return d.get("result", {})

    async def goto(self, url):
        await self.send("Page.navigate", {"url": url}); await asyncio.sleep(0.8)

    async def js(self, expr):
        r = await self.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
        if "exceptionDetails" in r: raise RuntimeError("js: " + json.dumps(r["exceptionDetails"])[:400])
        return r["result"].get("value")

    async def wait(self, expr, timeout=12, what=""):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if await self.js(f"Boolean({expr})"): return True
            except Exception: pass
            await asyncio.sleep(0.15)
        raise AssertionError(f"timed out waiting for {what or expr}\nerrors={self.errors}\nlogs={self.logs}")

    async def click(self, sel, text=None):
        q = f"[...document.querySelectorAll({json.dumps(sel)})].find(e => {('e.textContent.trim().startsWith(' + json.dumps(text) + ')') if text else 'true'})"
        await self.wait(q, what=f"{sel} {text or ''}")
        await self.js(f"{q}.click()")
        await asyncio.sleep(0.1)

    async def type(self, sel, text):
        await self.wait(f"document.querySelector({json.dumps(sel)})", what=sel)
        await self.js(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); e.focus(); e.value = {json.dumps(text)}; e.dispatchEvent(new Event('input', {{bubbles: true}})); }})()")

    async def shot(self, path):
        r = await self.send("Page.captureScreenshot", {"format": "png"})
        open(path, "wb").write(base64.b64decode(r["data"])); return path

    async def text(self, sel):
        return await self.js(r"[...document.querySelectorAll(" + json.dumps(sel) + r")].map(e => e.textContent.trim().replace(/\s+/g,' '))")
