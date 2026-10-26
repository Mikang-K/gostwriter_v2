import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

let dsn = process.env.TEST_DATABASE_URL;
if (!dsn) {
  try { dsn = JSON.parse(await readFile(".cache/development-db.json", "utf8")).testDatabaseUrl; }
  catch { throw new Error("Start npm run db:local or set isolated TEST_DATABASE_URL first."); }
}
if (!dsn) throw new Error("TEST_DATABASE_URL is required; integration tests must not silently skip.");
const python = resolve("backend/.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const child = spawn(python, ["-m", "pytest", "backend/tests", "-q"], { stdio: "inherit", windowsHide: true,
  env: { ...process.env, TEST_DATABASE_URL: dsn, PYTHONPATH: resolve("backend") } });
child.on("error", error => { console.error(error.message); process.exitCode = 1; });
child.on("exit", code => { process.exitCode = code ?? 1; });
