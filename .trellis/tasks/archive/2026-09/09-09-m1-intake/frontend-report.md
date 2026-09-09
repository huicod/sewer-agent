# T04 frontend report

## Delivered

- Added a minimal Chinese React/TypeScript intake page under `frontend/`.
- Creates or reloads a persisted browser session ID, uploads one evidence file, then submits an idempotent task request using the returned asset reference.
- A failed task creation reuses the same request UUID and asset only for the same `File` object, session and instruction. A different file is uploaded again even when browser metadata matches.
- Shows application/validation/network errors returned through the API boundary. Successful Session, Asset and Task JSON is structurally checked before it reaches React.
- Shows server task status and progress. Only queued/running/waiting-review tasks are polled.
- Queued copy is explicitly truthful: `已接收入队，检查执行尚未接入`. No assistant reply or execution result is synthesized.
- Vite dev/preview hosts are loopback-only; `/api` proxies to `http://127.0.0.1:8000`.

## Runtime and dependency lock

- Installed official Node.js `24.21.0` (LTS) under `.tools/node`; system Node was not replaced. npm is `11.19.0`.
- Current Vite requires Node `^20.19.0 || >=22.12.0`; Node 20 is now EOL, while Node 24 is LTS ([Vite compatibility](https://vite.dev/guide/#scaffolding-your-first-vite-project), [Node releases](https://nodejs.org/en/about/previous-releases)).
- Exact direct versions in lockfile v3: React/React DOM `19.2.8`, Vite `8.2.2`, TypeScript `7.0.2`, Vitest `5.0.0`, jsdom `30.0.1`, Testing Library React `16.3.3`, DOM `10.4.1`, user-event `14.6.7`, React types `19.2.18` and React DOM types `19.2.7`.
- `package.json` has no runtime state, router, query-cache, schema, styling or model/API dependency beyond React itself.

## TDD and verification evidence

- Initial RED: `npm test` -> 2 failed suites because `./App` and `./api` did not exist.
- Review RED: `npm test -- src/api-validation.test.ts src/retry-identity.test.tsx` -> 4 expected failures: three unchecked successful payloads and one same-metadata/different-file reuse failure.
- GREEN: `npm test` -> 4 files, 11 tests passed.
- Build: `npm run build` -> TypeScript clean; Vite 8.2.2 transformed 18 modules and built successfully.
- Tests cover upload/task request boundaries, safe server errors, session reload, queued state/progress, active-only polling, malformed 2xx JSON, and retry file identity.

## Local commands

```bash
export PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH
cd /root/autodl-tmp/server/frontend
npm ci
npm test
npm run build
```

## Boundary

- This is the T04 intake shell. Queued tasks are persisted by the server, but inspection/dialogue execution remains intentionally unconnected until later tasks.
- Acceptance evidence is component/API-boundary tests plus a production build; no browser-based visual run was available or claimed.
