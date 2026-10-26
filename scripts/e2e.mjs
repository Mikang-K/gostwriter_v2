import { spawn } from "node:child_process";
import { readFile, mkdir, open } from "node:fs/promises";
import { createServer } from "node:net";
import { resolve } from "node:path";

const config = JSON.parse(await readFile(".cache/development-db.json", "utf8"));
if (!new URL(config.testDatabaseUrl).pathname.startsWith("/ghostwriter_test")) throw new Error("Isolated test database required.");
for (const port of [3000, 8000, 11435]) await new Promise((ok, fail) => {
  const server = createServer(); server.once("error", () => fail(new Error(`Port ${port} must be free for isolated E2E servers.`)));
  server.listen(port, "127.0.0.1", () => server.close(ok));
});
await mkdir(".cache", { recursive: true });
const python = resolve("backend/.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const env = { ...process.env, PYTHONPATH: resolve("backend"), DATABASE_URL: config.testDatabaseUrl,
  APP_PASSWORD: config.appPassword, APP_ORIGIN: "http://localhost:3000", ENABLE_CLOUD: "false",
  OLLAMA_BASE_URL: "http://127.0.0.1:11435", OLLAMA_MODEL: "test-local", CONTEXT_WINDOW: "8192" };
const children = [], logs = [];
async function start(name, command, args) {
  const log = await open(`.cache/e2e-${name}.log`, "w"); logs.push(log);
  const child = spawn(command, args, { env, windowsHide: true, stdio: ["ignore", log.fd, log.fd] });
  children.push(child); child.on("error", () => {}); return child;
}
async function ready(url, child) {
  for (let i = 0; i < 100; i++) {
    if (child.exitCode !== null) throw new Error("E2E fixture server exited; see .cache/e2e-*.log");
    try { if ((await fetch(url)).ok) return; } catch {}
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error("E2E server did not become ready.");
}
try {
  const fake = await start("upstream", python, ["-m", "tests.fake_upstream"]);
  await ready("http://127.0.0.1:11435/stats", fake);
  const api = await start("api", python, ["-m", "app"]);
  await ready("http://127.0.0.1:8000/api/health", api);
  const web = await start("web", process.execPath, [resolve("node_modules/next/dist/bin/next"), "start", "--hostname", "127.0.0.1"]);
  await ready("http://127.0.0.1:3000/api/health", web);
  const cli = resolve("node_modules/@playwright/test/cli.js");
  const test = spawn(process.execPath, [cli, "test", ...process.argv.slice(2)], { env, windowsHide: true, stdio: "inherit" });
  children.push(test);
  process.exitCode = await new Promise((ok, fail) => { test.once("exit", code => ok(code ?? 1)); test.once("error", fail); });
} finally {
  for (const child of children.reverse()) if (child.exitCode === null) child.kill();
  for (const log of logs) await log.close();
}
