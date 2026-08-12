/**
 * sync-catalog.js
 * -----------------------------------------------------------------------------
 * Turns ../../catalog-data/architecture.json into an EventCatalog site using the
 * official @eventcatalog/sdk.
 *
 * READ THIS FIRST
 * ---------------
 * EventCatalog does NOT read our FastAPI source code. It has no idea that
 * camp-service publishes PatientRegistered. Something has to tell it, and that
 * something is this script plus architecture.json.
 *
 * architecture.json is a deliberate stand-in for what a future ITS RMS
 * "business module / event listing" admin form (or its database table) would
 * hand us. Swap the `loadArchitecture()` function for a database query or an
 * HTTP call and the rest of this script keeps working unchanged.
 *
 * The script is idempotent: run it as often as you like. It wipes the generated
 * folders first so deletions in architecture.json actually disappear from the site.
 *
 * Usage:  npm run sync
 */

const fs = require('node:fs');
const path = require('node:path');

// The SDK ships CommonJS + ESM. We are CommonJS here (eventcatalog.config.js
// uses module.exports, so this package must not be "type": "module").
const sdkModule = require('@eventcatalog/sdk');
const utils = sdkModule.default || sdkModule;

const CATALOG_DIR = path.resolve(__dirname, '..');
const ARCHITECTURE_FILE = path.resolve(CATALOG_DIR, '..', 'catalog-data', 'architecture.json');

// Folders this script owns end-to-end. Anything you hand-edit in here is lost
// on the next sync - that is intentional.
const GENERATED_DIRS = ['domains', 'services', 'events', 'channels'];

const CHANNEL_ID = 'its-rms-events';

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
const log = {
  step: (msg) => console.log(`\n\x1b[1m${msg}\x1b[0m`),
  ok: (msg) => console.log(`  \x1b[32m+\x1b[0m ${msg}`),
  info: (msg) => console.log(`  \x1b[36mi\x1b[0m ${msg}`),
  warn: (msg) => console.warn(`  \x1b[33m!\x1b[0m ${msg}`),
  fail: (msg) => console.error(`  \x1b[31mx\x1b[0m ${msg}`),
};

/** Arrays of strings in architecture.json are joined into markdown paragraphs. */
const md = (value) => (Array.isArray(value) ? value.join('\n') : value || '');

/**
 * THE SWAP POINT.
 * Today: read a JSON file checked into the repo.
 * Tomorrow: `return await db.query('select * from business_module_events')`.
 */
function loadArchitecture() {
  if (!fs.existsSync(ARCHITECTURE_FILE)) {
    throw new Error(`Cannot find ${ARCHITECTURE_FILE}. Run this from the eventcatalog/ folder.`);
  }
  return JSON.parse(fs.readFileSync(ARCHITECTURE_FILE, 'utf-8'));
}

/**
 * Fail loudly on a broken architecture.json rather than building a wrong map.
 * A catalog that quietly lies is worse than no catalog.
 */
function validate(arch) {
  const errors = [];
  const domainIds = new Set(arch.domains.map((d) => d.id));
  const serviceIds = new Set(arch.services.map((s) => s.id));
  const eventIds = new Set(arch.events.map((e) => e.id));

  for (const service of arch.services) {
    if (!domainIds.has(service.domain)) {
      errors.push(`service '${service.id}' points at unknown domain '${service.domain}'`);
    }
    for (const id of [...(service.publishes || []), ...(service.consumes || [])]) {
      if (!eventIds.has(id)) errors.push(`service '${service.id}' references unknown event '${id}'`);
    }
  }

  for (const event of arch.events) {
    if (!serviceIds.has(event.producedBy)) {
      errors.push(`event '${event.id}' is producedBy unknown service '${event.producedBy}'`);
    }
    for (const id of event.consumedBy || []) {
      if (!serviceIds.has(id)) errors.push(`event '${event.id}' is consumedBy unknown service '${id}'`);
    }
    // Both directions must agree, otherwise the map and the code drift apart.
    const producer = arch.services.find((s) => s.id === event.producedBy);
    if (producer && !(producer.publishes || []).includes(event.id)) {
      errors.push(`event '${event.id}' says producedBy '${producer.id}', but that service does not list it in publishes`);
    }
    for (const consumerId of event.consumedBy || []) {
      const consumer = arch.services.find((s) => s.id === consumerId);
      if (consumer && !(consumer.consumes || []).includes(event.id)) {
        errors.push(`event '${event.id}' says consumedBy '${consumerId}', but that service does not list it in consumes`);
      }
    }
  }

  if (errors.length) {
    log.fail('architecture.json is inconsistent:');
    errors.forEach((e) => console.error(`      - ${e}`));
    throw new Error('Fix catalog-data/architecture.json and run npm run sync again.');
  }
  log.ok(`architecture.json is consistent (${arch.domains.length} domains, ${arch.services.length} services, ${arch.events.length} events)`);
}

function cleanGeneratedFolders() {
  for (const dir of GENERATED_DIRS) {
    const target = path.join(CATALOG_DIR, dir);
    if (fs.existsSync(target)) {
      fs.rmSync(target, { recursive: true, force: true });
      log.info(`cleared generated folder ./${dir}`);
    }
  }
}

// ---------------------------------------------------------------------------
// markdown builders (what a human actually reads on each page)
// ---------------------------------------------------------------------------
function domainMarkdown(domain, arch) {
  const services = arch.services.filter((s) => s.domain === domain.id);
  const rows = services
    .map((s) => `| [${s.name}](/docs/services/${s.id}/${s.version}) | \`${s.port}\` | ${(s.publishes || []).join(', ') || '-'} | ${(s.consumes || []).join(', ') || '-'} |`)
    .join('\n');

  return `## Overview

${md(domain.description)}

### Services in this domain

| Service | Local port | Publishes | Consumes |
| --- | --- | --- | --- |
${rows}

### Domain map

<NodeGraph />

---

*Generated by \`eventcatalog/scripts/sync-catalog.js\` from \`catalog-data/architecture.json\`. Do not edit by hand.*
`;
}

function serviceMarkdown(service, arch) {
  const endpoints = (service.endpoints || [])
    .map((e) => `| \`${e.method}\` | \`${e.path}\` | ${e.description} |`)
    .join('\n');

  const eventLine = (id, direction) => {
    const event = arch.events.find((e) => e.id === id);
    return `- **${direction}** [\`${id}\`](/docs/events/${id}/${event.version}) on routing key \`${event.routingKey}\` - ${event.summary}`;
  };

  const publishes = (service.publishes || []).map((id) => eventLine(id, 'publishes')).join('\n');
  const consumes = (service.consumes || []).map((id) => eventLine(id, 'consumes')).join('\n');

  return `## Overview

${service.summary}

Runs locally on port \`${service.port}\`. Interactive API docs: [${service.docsUrl}](${service.docsUrl}) (only while \`docker compose up\` is running).

### Messages

${publishes || '_Publishes nothing._'}
${consumes || '_Consumes nothing._'}

### HTTP endpoints

| Method | Path | Purpose |
| --- | --- | --- |
${endpoints}

### Service map

<NodeGraph />

---

*Generated by \`eventcatalog/scripts/sync-catalog.js\` from \`catalog-data/architecture.json\`. Do not edit by hand.*
`;
}

function eventMarkdown(event, arch) {
  const consumers = (event.consumedBy || []).length
    ? event.consumedBy.map((id) => `\`${id}\``).join(', ')
    : '_nobody yet - published for future use_';

  return `## Overview

${md(event.description)}

| | |
| --- | --- |
| **Broker** | RabbitMQ, topic exchange \`${arch.catalog.broker.exchange}\` |
| **Routing key** | \`${event.routingKey}\` |
| **Produced by** | \`${event.producedBy}\` |
| **Consumed by** | ${consumers} |

### Flow

<NodeGraph />

### Payload schema

<SchemaViewer file="schema.json" title="JSON Schema" maxHeight="500" />

### Example message

\`\`\`json
${JSON.stringify(event.example, null, 2)}
\`\`\`

---

*Generated by \`eventcatalog/scripts/sync-catalog.js\` from \`catalog-data/architecture.json\`. Do not edit by hand.*
`;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------
async function main() {
  log.step('1/6  Reading architecture metadata');
  const arch = loadArchitecture();
  log.info(`source: ${path.relative(process.cwd(), ARCHITECTURE_FILE)}`);
  validate(arch);

  log.step('2/6  Clearing previously generated catalog content');
  cleanGeneratedFolders();

  const {
    writeDomain,
    writeServiceToDomain,
    writeEventToService,
    addSchemaToEvent,
    writeChannel,
  } = utils(CATALOG_DIR);

  // -- channel: the RabbitMQ topic exchange every message travels through -----
  log.step('3/6  Writing the RabbitMQ channel');
  await writeChannel(
    {
      id: CHANNEL_ID,
      name: `${arch.catalog.broker.exchange} (RabbitMQ topic exchange)`,
      version: arch.catalog.version,
      summary: 'Single topic exchange carrying every event in this POC.',
      address: arch.catalog.broker.exchange,
      protocols: ['amqp'],
      parameters: {
        routingKey: {
          description: 'Topic routing key the message was published with.',
          enum: arch.events.map((e) => e.routingKey),
        },
      },
      markdown: `## Overview

Every event in this POC is published to a single RabbitMQ **topic exchange** called \`${arch.catalog.broker.exchange}\`.
Consumers create a durable queue and bind it to the routing keys they care about.

| Event | Routing key |
| --- | --- |
${arch.events.map((e) => `| \`${e.id}\` | \`${e.routingKey}\` |`).join('\n')}

Management UI while the stack is running: [${arch.catalog.broker.managementUi}](${arch.catalog.broker.managementUi}) (guest / guest).
`,
    },
    { override: true }
  );
  log.ok(`channel ${CHANNEL_ID}`);

  // -- domains ---------------------------------------------------------------
  log.step('4/6  Writing domains');
  for (const domain of arch.domains) {
    const domainServices = arch.services.filter((s) => s.domain === domain.id);
    await writeDomain(
      {
        id: domain.id,
        name: domain.name,
        version: domain.version,
        summary: domain.summary,
        owners: domain.owners || [],
        styles: domain.styles,
        // This is what draws the domain -> service edges on the map.
        services: domainServices.map((s) => ({ id: s.id, version: s.version })),
        markdown: domainMarkdown(domain, arch),
      },
      { override: true }
    );
    log.ok(`domain ${domain.name}  (${domainServices.map((s) => s.id).join(', ')})`);
  }

  // -- services (written inside their domain folder) --------------------------
  log.step('5/6  Writing services and their publish/consume relationships');
  for (const service of arch.services) {
    const pointer = (id) => {
      const event = arch.events.find((e) => e.id === id);
      return { id: event.id, version: event.version };
    };
    await writeServiceToDomain(
      {
        id: service.id,
        name: service.name,
        version: service.version,
        summary: service.summary,
        styles: service.styles,
        repository: service.repository,
        // sends    = events this service PUBLISHES
        // receives = events this service CONSUMES
        sends: (service.publishes || []).map(pointer),
        receives: (service.consumes || []).map(pointer),
        markdown: serviceMarkdown(service, arch),
      },
      { id: service.domain },
      { override: true }
    );
    log.ok(
      `service ${service.id}  sends=[${(service.publishes || []).join(', ')}]  receives=[${(service.consumes || []).join(', ')}]`
    );
  }

  // -- events (owned by the service that publishes them) ----------------------
  log.step('6/6  Writing events and JSON schemas');
  for (const event of arch.events) {
    const producer = arch.services.find((s) => s.id === event.producedBy);
    await writeEventToService(
      {
        id: event.id,
        name: event.name,
        version: event.version,
        summary: event.summary,
        styles: event.styles,
        schemaPath: 'schema.json',
        channels: [{ id: CHANNEL_ID, version: arch.catalog.version, parameters: { routingKey: event.routingKey } }],
        badges: [
          { content: `routing key: ${event.routingKey}`, backgroundColor: 'gray', textColor: 'gray' },
          { content: 'RabbitMQ', backgroundColor: 'orange', textColor: 'orange' },
        ],
        markdown: eventMarkdown(event, arch),
      },
      { id: producer.id },
      { override: true }
    );

    await addSchemaToEvent(
      event.id,
      { schema: JSON.stringify(event.schema, null, 2), fileName: 'schema.json' },
      event.version
    );
    log.ok(`event ${event.id}  (published by ${producer.id}, schema.json attached)`);
  }

  // -- summary ---------------------------------------------------------------
  console.log('\n\x1b[1mCatalog written. The map will show:\x1b[0m');
  for (const domain of arch.domains) {
    console.log(`\n  ${domain.name}`);
    for (const service of arch.services.filter((s) => s.domain === domain.id)) {
      for (const id of service.publishes || []) console.log(`    ${service.id} --publishes--> ${id}`);
      for (const id of service.consumes || []) console.log(`    ${service.id} <--consumes--- ${id}`);
    }
  }
  console.log('\nNext:  npm run build   then   npm run preview\n');
}

main().catch((error) => {
  log.fail(error.message);
  process.exit(1);
});
