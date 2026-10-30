// One-off: drive the artifact's own canonical export via CDP, then publish
// clean SVG/PNG assets for README embedding (no viewer chrome).
import { spawn } from "node:child_process";
import { createReadStream } from "node:fs";
import { copyFile, mkdir, mkdtemp, readdir, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

const chromePath = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const port = 9333;
const folder = ".archify/architecture-stock-analyze-runtime-20261030-120956";
const artifact = path.resolve(folder, "stock-analyze-runtime.html");
const outDir = path.resolve("docs/architecture/diagrams");
const userDir = await mkdtemp(path.join(tmpdir(), "archify-cdp-"));
const downloadDir = await mkdtemp(path.join(tmpdir(), "archify-dl-"));
await mkdir(outDir, { recursive: true });

const chromeArgs = [
  "--headless=new",
  `--remote-debugging-port=${port}`,
  "--remote-allow-origins=*",
  "--no-sandbox",
  "--disable-gpu",
  "--hide-scrollbars",
  `--user-data-dir=${userDir}`,
  `file://${artifact}`,
];
const child = spawn(chromePath, chromeArgs, { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function pageTarget() {
  for (let i = 0; i < 100; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`);
      const list = await res.json();
      const page = list.find(
        (t) => t.type === "page" && t.url.startsWith("file://") && t.webSocketDebuggerUrl
      );
      if (page) return page;
    } catch {
      // chrome not up yet
    }
    await sleep(100);
  }
  throw new Error("no CDP page target");
}

const target = await pageTarget();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.addEventListener("open", resolve, { once: true });
  ws.addEventListener("error", reject, { once: true });
});
let seq = 0;
const pending = new Map();
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    if (msg.error) reject(new Error(JSON.stringify(msg.error)));
    else resolve(msg.result);
  }
});
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });

await send("Page.enable");
await send("Runtime.enable");
await send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloadDir });

const evaluate = async (expression) => {
  const { result, exceptionDetails } = await send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (exceptionDetails) throw new Error(JSON.stringify(exceptionDetails));
  return result.value;
};

await evaluate(`new Promise((resolve, reject) => {
  const started = Date.now();
  const tick = () => {
    if (window.Archify && Archify.exportMenu && typeof Archify.exportMenu.run === "function") return resolve(true);
    if (Date.now() - started > 20000) return reject(new Error("viewer not ready"));
    setTimeout(tick, 100);
  };
  tick();
})`);

for (const format of ["svg-light", "svg-dark", "png"]) {
  await evaluate(`Archify.exportMenu.run(${JSON.stringify(format)}).then(() => true)`);
}

async function waitForFile(name, timeout = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const entries = await readdir(downloadDir);
    const match = entries.find((e) => e === name);
    if (match) {
      const info = await stat(path.join(downloadDir, match));
      await sleep(300);
      const again = await stat(path.join(downloadDir, match));
      if (again.size === info.size && info.size > 0) return path.join(downloadDir, match);
    }
    await sleep(200);
  }
  throw new Error(`download ${name} not found: ${(await readdir(downloadDir)).join(",")}`);
}

const files = {
  svgLight: await waitForFile("stock-analyze-light.svg"),
  svgDark: await waitForFile("stock-analyze-dark.svg"),
  png: await waitForFile("stock-analyze.png"),
};

for (const [key, src] of Object.entries(files)) {
  const size = (await stat(src)).size;
  console.log(`${key}: ${src} (${size} bytes)`);
}

await copyFile(files.svgLight, path.join(outDir, "stock-analyze-runtime.svg"));
await copyFile(files.png, path.join(outDir, "stock-analyze-runtime.png"));
await copyFile(artifact, path.join(outDir, "stock-analyze-runtime.html"));
await copyFile(
  path.resolve(folder, "candidate.json"),
  path.join(outDir, "stock-analyze-runtime.spec.json")
);
console.log("published to", outDir);

ws.close();
child.kill("SIGTERM");
