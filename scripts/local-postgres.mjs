import EmbeddedPostgres from "embedded-postgres";
import { mkdir, readFile, writeFile, access } from "node:fs/promises";
import { join, resolve } from "node:path";
import { homedir } from "node:os";
import { randomBytes } from "node:crypto";

const root = process.cwd();
const cache = resolve(root, ".cache");
await mkdir(cache, { recursive: true });
const file = join(cache, "development-db.json");
let config;
try { config = JSON.parse(await readFile(file, "utf8")); }
catch (error) {
  if (error.code !== "ENOENT") throw error;
  config = { port: 55432, password: randomBytes(24).toString("hex"),
    databaseDir: join(process.env.LOCALAPPDATA || join(homedir(), ".local", "share"), "ghostwriter-v2", "postgres"),
    appPassword: randomBytes(18).toString("base64url") };
  await writeFile(file, JSON.stringify(config), { mode: 0o600, flag: "wx" });
}
const pg = new EmbeddedPostgres({ ...config, user: "ghostwriter", persistent: true,
  authMethod: "scram-sha-256", postgresFlags: ["-h", "127.0.0.1"],
  onLog: () => {}, onError: () => {} });
try { await access(join(config.databaseDir, "PG_VERSION")); }
catch { await pg.initialise(); }
await pg.start();
for (const name of ["ghostwriter_v2", "ghostwriter_test"]) {
  const client = pg.getPgClient("postgres", "127.0.0.1");
  await client.connect();
  const found = await client.query("select 1 from pg_database where datname=$1", [name]);
  await client.end();
  if (!found.rowCount) await pg.createDatabase(name);
}
const dsn = name => `postgresql://ghostwriter:${config.password}@127.0.0.1:${config.port}/${name}`;
config.databaseUrl = dsn("ghostwriter_v2");
config.testDatabaseUrl = dsn("ghostwriter_test");
await writeFile(file, JSON.stringify(config), { mode: 0o600 });
try { await access(join(root, "backend", ".env.local")); }
catch {
  await writeFile(join(root, "backend", ".env.local"),
    `DATABASE_URL=${config.databaseUrl}\nAPP_PASSWORD=${config.appPassword}\nAPP_ORIGIN=http://localhost:3000\n`,
    { mode: 0o600, flag: "wx" });
}
console.log("PostgreSQL ready on 127.0.0.1:55432. Local credentials: backend/.env.local");
console.log("Test credentials: .cache/development-db.json. Ctrl+C stops this server; data is retained.");
let stopping = false;
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, async () => {
  if (stopping) return;
  stopping = true;
  await pg.stop();
  process.exit(0);
});
setInterval(() => {}, 60000);
