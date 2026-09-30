// Disposable local D1 only. JSON-lines bridge for CPython route tests.
// Usage: node local_d1_bridge.mjs /absolute/path/to/miniflare/index.js
import { createRequire } from 'node:module';
import { createInterface } from 'node:readline';
const require = createRequire(import.meta.url);
const { Miniflare, convertV4MiniflareOptions } = require(process.argv[2]);
const options = {
  host: '127.0.0.1', port: 0, modules: true, cf: false,
  script: 'export default { fetch() { return new Response("local audit"); } };',
  d1Databases: { DB: 'synthetic-match-mutation' },
  d1Persist: false,
  outboundService() { throw new Error('External network forbidden'); },
};
const mf = new Miniflare(convertV4MiniflareOptions ?
  { ...convertV4MiniflareOptions(options), telemetry: { enabled: false } } : options);
try {
  const db = await mf.getD1Database('DB');
  for await (const line of createInterface({ input: process.stdin })) {
    const request = JSON.parse(line);
    if (request.method === 'close') break;
    try {
      let value;
      if (request.method === 'batch') {
        value = await db.batch(request.statements.map(({ sql, params }) => db.prepare(sql).bind(...params)));
      } else {
        const stmt = db.prepare(request.sql).bind(...request.params);
        value = await stmt[request.method]();
      }
      process.stdout.write(JSON.stringify({ value }) + '\n');
    } catch (error) {
      process.stdout.write(JSON.stringify({ error: String(error) }) + '\n');
    }
  }
} finally {
  await mf.dispose();
}
