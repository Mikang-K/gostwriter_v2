import { spawn } from "node:child_process";
import { resolve } from "node:path";

const python = resolve("backend/.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const child = spawn(python, ["-m", "app"], { stdio: "inherit", env: { ...process.env, PYTHONPATH: resolve("backend") }, windowsHide: true });
child.on("error", error => { console.error(error.message); process.exitCode = 1; });
child.on("exit", code => { process.exitCode = code ?? 1; });
