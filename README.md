# ITS RMS EventCatalog POC

A deliberately small, runnable proof of concept for an **ITS RMS healthcare medical-camp** system.

It does two things:

1. Runs **three FastAPI microservices** that talk to each other through **RabbitMQ events** — a realistic medical camp flow, end to end.
2. Generates an **EventCatalog website** that draws a visual map of those domains, services and events.

> ### Everything here is fictional
>
> Every patient, doctor, camp, phone number and diagnosis in this project is **made-up sample data**
> created for a demo. There is no real patient data, no real ITS integration, no AI, and no
> production authentication anywhere in this repository. Nothing here is medical advice.

---

## Table of contents

- [The idea in one picture](#the-idea-in-one-picture)
- [Domains, services and events](#domains-services-and-events)
- [The business flow](#the-business-flow)
- [Project folders](#project-folders)
- [Prerequisites](#prerequisites)
- [Run it (Windows PowerShell)](#run-it-windows-powershell)
- [Run the end-to-end test script](#run-the-end-to-end-test-script)
- [Watch the events with your own eyes](#watch-the-events-with-your-own-eyes)
- [Generate the EventCatalog website](#generate-the-eventcatalog-website)
- [How the catalog actually gets its data](#how-the-catalog-actually-gets-its-data)
- [Troubleshooting](#troubleshooting)
- [Deliberately not implemented](#deliberately-not-implemented)

---

## The idea in one picture

```
        Identity & Access              Camp Operations                 Clinical Care
        ─────────────────              ───────────────                 ─────────────
        identity-service               camp-service                    clinical-service
            :8001                         :8002                            :8003
              │                             │                                │
              │  UserLoggedIn               │                                │
              │  (user.logged-in)           │                                │
              ├────────────────────────────►│  (logging only)                │
              │                             │                                │
              │                             │  PatientRegistered             │
              │                             │  (patient.registered)          │
              │                             ├───────────────────────────────►│  opens a case
              │                             │                                │
              │                             │  SlotBooked                    │
              │                             │  (slot.booked)                 │
              │                             ├───────────────────────────────►│  attaches the slot
              │                             │                                │
              │                             │            VitalsRecorded      │
              │                             │            (vitals.recorded)   │◄─┐
              │                             │                                │  │ doctor
              │                             │            CaseClosed          │  │ works
              │                             │            (case.closed)       │◄─┘
              │                             │                                │
              └───────────── all messages travel through one RabbitMQ ───────┘
                             topic exchange: its.rms.events
```

**A domain** is a business area. **A service** is one deployable app inside a domain.
**An event** is a past-tense fact one service announces and any other service may listen to.

The important part: `clinical-service` **never calls** `camp-service`. It finds out about patients
purely by listening. That is what "event-driven" buys you, and it is what the catalog map shows.

---

## Domains, services and events

| Domain | Service | Port | Publishes | Consumes |
| --- | --- | --- | --- | --- |
| Identity & Access | `identity-service` | 8001 | `UserLoggedIn` | — |
| Camp Operations | `camp-service` | 8002 | `PatientRegistered`, `SlotBooked` | `UserLoggedIn` (logging only) |
| Clinical Care | `clinical-service` | 8003 | `VitalsRecorded`, `CaseClosed` | `PatientRegistered`, `SlotBooked` |

### The five events

| Event | Routing key | Meaning |
| --- | --- | --- |
| `UserLoggedIn` | `user.logged-in` | A fictional user signed in; their roles were resolved |
| `PatientRegistered` | `patient.registered` | A fictional patient was registered against a camp |
| `SlotBooked` | `slot.booked` | A consultation slot was booked for that patient |
| `VitalsRecorded` | `vitals.recorded` | A doctor saved temperature, blood pressure and weight |
| `CaseClosed` | `case.closed` | The consultation finished and the case was closed |

All five go to a single RabbitMQ **topic exchange** called `its.rms.events`. Consumers declare a
durable queue and bind it to the routing keys they care about.

`VitalsRecorded` and `CaseClosed` have **no consumer** in this POC. That is on purpose — it shows
what an available-but-unused event looks like on the catalog map.

### Roles

`Super Admin`, `Admin`, `Data Entry`, `Doctor`, `Patient`.

One phone number can hold several roles, so login and role selection are one combined step.
The fictional demo logins (see `GET http://localhost:8001/demo-users`):

| Phone (fictional) | Roles |
| --- | --- |
| `+919999999999` | **Admin + Data Entry** — the multi-role demo user |
| `+919000000000` | Super Admin |
| `+919888888888` | Doctor |
| `+919777777777` | Patient |

---

## The business flow

1. **Log in.** A user enters a phone number. `identity-service` returns the roles that phone holds
   plus a fake session token, and publishes `UserLoggedIn`.
2. **Pick a role.** The user chooses `Data Entry` for this session.
3. **Configure a camp.** An admin creates a camp: name, host city, location, start/end dates,
   timezone, and the departments offered.
4. **Register a patient.** A Data Entry operator registers a fictional patient against that camp.
   `camp-service` publishes `PatientRegistered`.
5. **A case opens by itself.** `clinical-service` hears `PatientRegistered` and opens a clinical
   case. There is no HTTP endpoint to create a case — the event is the only way one appears.
6. **Book a slot.** A slot is booked for a department, date and session. `camp-service` publishes
   `SlotBooked`, and `clinical-service` attaches it to the open case.
7. **Doctor records vitals.** `clinical-service` publishes `VitalsRecorded`.
8. **Doctor records a diagnosis.** Chief complaint, diagnosis, medication, follow-up date.
9. **Doctor closes the case.** `clinical-service` publishes `CaseClosed`.

`scripts/test-flow.ps1` does exactly these nine steps and prints every response.

---

## Project folders

```
its-rms-eventcatalog-poc/
├── docker-compose.yml          RabbitMQ + the three services
├── README.md                   this file
│
├── identity-service/           Identity & Access domain  (port 8001)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py             /login, /select-role, /health
│       └── eventbus.py         tiny RabbitMQ publish/consume helper
│
├── camp-service/               Camp Operations domain    (port 8002)
│   └── app/main.py             /camps, /patients/register, /slots/book
│
├── clinical-service/           Clinical Care domain      (port 8003)
│   └── app/main.py             /cases/{id}/vitals, /diagnosis, /close
│
├── catalog-data/
│   └── architecture.json       ← the single source of truth for the catalog
│
├── eventcatalog/
│   ├── package.json
│   ├── eventcatalog.config.js
│   ├── scripts/
│   │   └── sync-catalog.js     reads architecture.json, writes the catalog
│   ├── domains/                GENERATED by npm run sync — do not hand-edit
│   ├── channels/               GENERATED
│   └── dist/                   GENERATED by npm run build
│
└── scripts/
    └── test-flow.ps1           end-to-end PowerShell walkthrough
```

`eventbus.py` is intentionally **copied** into all three services rather than shared. Each service
builds into its own Docker image, and 150 duplicated lines keeps every Dockerfile trivial. A real
system would publish it as a small internal library.

---

## Prerequisites

- **Docker Desktop** for Windows (running)
- **Node.js 20 or newer** — only needed for the EventCatalog website
- **Windows PowerShell 5.1** or **PowerShell 7+** — for the test script

You do **not** need Python installed. The services run inside containers.

---

## Run it (Windows PowerShell)

From the project root:

```powershell
# 1. Build the images and start RabbitMQ + all three services
docker compose up -d --build

# 2. Watch them come up (RabbitMQ has a healthcheck, the services wait for it)
docker compose ps

# 3. Follow the logs in a second window - this is where the event flow is visible
docker compose logs -f
```

Once everything reports healthy:

| What | Where |
| --- | --- |
| RabbitMQ management UI | <http://localhost:15672> — user `guest`, password `guest` |
| identity-service API docs | <http://localhost:8001/docs> |
| camp-service API docs | <http://localhost:8002/docs> |
| clinical-service API docs | <http://localhost:8003/docs> |

To stop everything:

```powershell
docker compose down

# or, to also delete the RabbitMQ container's state
docker compose down -v
```

---

## Run the end-to-end test script

With the stack running:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-flow.ps1
```

It waits for all three `/health` endpoints, then walks the nine steps above and prints every API
response. It finishes with a closed case and a summary of the events that travelled through the
exchange.

---

## Watch the events with your own eyes

Every service logs a line when it publishes and when it consumes:

```powershell
docker compose logs camp-service clinical-service | Select-String "PUBLISH|CONSUME|FLOW"
```

You should see pairs like:

```
[PUBLISH] camp-service -> exchange=its.rms.events routing_key=patient.registered event=PatientRegistered payload={...}
[CONSUME] clinical-service <- queue=clinical-service.patient-registered routing_key=patient.registered event=PatientRegistered payload={...}
[FLOW]    PatientRegistered -> opened clinical case CASE-0001 for patient PAT-0001
```

In the RabbitMQ UI at <http://localhost:15672> you can see the `its.rms.events` topic exchange,
the three consumer queues, and the message counters moving as you run the test script.

---

## Generate the EventCatalog website

```powershell
cd eventcatalog
npm install
npm run sync
npm run build
npx astro preview --host 127.0.0.1 --port 3000
```

Then open <http://127.0.0.1:3000>.

> `npx astro preview` prints a harmless `[WARN] Missing pages directory: src/pages` line — the site
> still serves correctly, because the pages come from EventCatalog's own build output. If you would
> rather not see it, EventCatalog ships the same preview server as a wrapper:
>
> ```powershell
> npm run preview
> ```
>
> For live-reloading while you edit `architecture.json`, use `npm run dev` instead of build+preview.

`npm run generate` is a shortcut for `npm run sync && npm run build`.

### What you will see

- **Domains** — Identity & Access, Camp Operations, Clinical Care
- **Services** — each with its publishes/consumes lists, its HTTP endpoints and its local port
- **Events** — each with its routing key, its JSON Schema and an example message
- **Visualiser** — the node graph that shows exactly which service publishes and consumes what

The catalog does **not** need the Docker stack to be running. They are independent.

---

## How the catalog actually gets its data

This is the part worth understanding.

### `architecture.json` is a stand-in for a future form or database

`catalog-data/architecture.json` describes the whole architecture: domains, services, events,
who publishes what, who consumes what, and a JSON Schema per event payload.

Today it is a hand-maintained file. It is deliberately shaped like **what a future ITS RMS
"business module / event listing" admin form — or the database table behind it — would give us**.
When that form exists, you replace one function in `eventcatalog/scripts/sync-catalog.js`:

```js
function loadArchitecture() {
  return JSON.parse(fs.readFileSync(ARCHITECTURE_FILE, 'utf-8'));   // today
  // return await db.query('select * from business_module_events');  // tomorrow
}
```

Everything downstream keeps working unchanged.

### EventCatalog does not read your FastAPI code

EventCatalog has **no idea** that `camp-service` publishes `PatientRegistered`. It does not parse
Python, does not import your routers, and does not watch RabbitMQ. Something has to tell it, and in
this project that something is `sync-catalog.js` reading `architecture.json` and calling the
official [`@eventcatalog/sdk`](https://www.eventcatalog.dev/docs/sdk).

That is why the MDX files under `eventcatalog/domains/` are **generated, not hand-maintained**.
`npm run sync` deletes and rewrites them every time, so anything you type in there by hand is lost.
Edit `architecture.json` instead.

The sync script also **validates** `architecture.json` before writing anything: if a service claims
to publish an event that says it is produced by someone else, the script fails loudly rather than
drawing a map that quietly lies.

### The map is not live

The catalog is a **static site built from a snapshot**. It updates when you run:

```powershell
npm run sync    # architecture.json  ->  MDX + JSON schemas
npm run build   # MDX               ->  static site in ./dist
```

It does **not** update by itself while the services run. Publishing a new event at runtime changes
nothing on the map until you edit `architecture.json` and re-run those two commands. Treat the site
as documentation that you regenerate, not as a monitoring dashboard.

---

## Troubleshooting

**`docker compose up` fails on a port that is already in use**
Something else on your machine owns 5672, 15672, 8001, 8002 or 8003. Find it with
`netstat -ano | Select-String ":8002"`, then either stop it or change the left-hand side of the
port mapping in `docker-compose.yml` (e.g. `"18002:8002"`).

**A service log shows `cannot reach RabbitMQ (attempt 3/60) ... retrying in 2.0s`**
This is normal for the first few seconds. Each service retries the connection up to 60 times, two
seconds apart, so it can start before the broker is ready. If it never stops, RabbitMQ itself is
failing — check `docker compose logs rabbitmq`.

**`test-flow.ps1` fails with "did not become healthy"**
The stack is not up yet, or a container crashed. Run `docker compose ps` and
`docker compose logs --tail 50`.

**`test-flow.ps1` fails with "clinical-service never opened a case"**
`camp-service` published but `clinical-service` did not consume. Check that both show
`connected to RabbitMQ` in their logs, and look at <http://localhost:15672> → Queues for
`clinical-service.patient-registered`. If the queue has unacknowledged messages, check
`docker compose logs clinical-service` for a handler error.

**PowerShell says the script "is not digitally signed"**
Use the exact command above with `-ExecutionPolicy Bypass`, which applies only to that one run.

**Everything disappears after `docker compose restart`**
Expected. All three services store data in plain in-memory dictionaries. Restarting a container
wipes its camps, patients and cases. Re-run `test-flow.ps1` to recreate them.

**`npm run sync` fails with "architecture.json is inconsistent"**
The validator found a contradiction — for example an event listed under `publishes` that does not
exist, or a `consumedBy` that the consuming service does not list in its own `consumes`. The error
message names the exact problem. Fix `catalog-data/architecture.json` and run it again.

**`npm run build` prints `[ERROR] [content] Invalid content reference ... in collection "schemas"`**
This is a known cosmetic message from EventCatalog 4.6.x itself. It appears whenever a JSON Schema
is attached to a message, including on a brand-new empty catalog, and it is not caused by anything
in this project. The build still finishes successfully (exit code 0), all pages render, and the
schemas display correctly. Safe to ignore.

**The catalog does not show a change I made**
You edited a generated MDX file instead of `architecture.json`, or you did not re-run
`npm run sync && npm run build`. See [the map is not live](#the-map-is-not-live).

**Port 3000 is busy when previewing the catalog**
Change it: `npx astro preview --host 127.0.0.1 --port 3100`.

---

## Deliberately not implemented

This POC exists to demonstrate a shape, not to be a product. The following are **out of scope on
purpose**, and should not be read as gaps to fill in:

- Real ITS integration — patient type is just a string, `"ITS member"` or `"non-ITS member"`
- Real authentication — no passwords, no OTP, no JWT signing, no session expiry
- Real AI features
- Real patient data — every record is fictional sample data, and every event carries `sampleData: true`
- File uploads and document handling
- The full PM requirement set — camps, registration, slots, vitals, diagnosis and case closure only
- Any real database — plain in-memory Python dictionaries, cleared on restart
- Authorization enforcement — roles are returned and selected, but no endpoint checks them
- Retries, dead-letter queues, idempotency keys, or any other production messaging concern
