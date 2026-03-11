import type { Request, Response, Router } from 'express';
import type {
  BotConfig,
  Event,
  EventHandler,
  EventKind,
  HandlerOptions,
  InvokeOptions,
  Platform,
  PlatformCapabilities,
  RunChunk,
  RunResult,
} from './types.js';

// The napi native module. Loaded lazily so tests can inject a mock.
let native: any;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  native = require('@botmux/native');
} catch {
  // Not available — must be set via setNativeModule before creating a Bot.
  native = undefined;
}

/** @internal Replace the native module (used by tests). */
export function setNativeModule(mod: any): void {
  native = mod;
}

function assertNativeAvailable(): void {
  if (native) {
    return;
  }
  throw new Error(
    'botmux native bindings are unavailable. Install @botmux/native or inject a test double with setNativeModule().',
  );
}

/** @internal Convert snake_case napi event JSON to camelCase Event with methods. */
function toEvent(raw: Record<string, any>, bot: Bot): Event {
  const platform: PlatformCapabilities = {
    name: raw.platform?.name as Platform,
    ephemeral: raw.platform?.ephemeral ?? false,
    threads: raw.platform?.threads ?? false,
    reactions: raw.platform?.reactions ?? false,
    streaming: raw.platform?.streaming ?? false,
    modals: raw.platform?.modals ?? false,
    typingIndicator: raw.platform?.typing_indicator ?? false,
  };

  const internalThreadId = native.deterministicThreadId(
    platform.name,
    raw.workspace_id ?? '',
    raw.channel_id ?? '',
    raw.thread_id ?? '',
  );

  const event: Event = {
    kind: raw.kind as EventKind,
    platform,
    workspaceId: raw.workspace_id ?? '',
    channelId: raw.channel_id ?? '',
    threadId: raw.thread_id ?? '',
    messageId: raw.message_id ?? '',
    user: {
      id: raw.user?.id ?? '',
      name: raw.user?.name,
      email: raw.user?.email,
    },
    text: raw.text ?? '',
    command: raw.command,
    emoji: raw.emoji,
    rawEventType: raw.raw_event_type,
    raw: raw.raw,
    internalThreadId,

    async invoke(agent: string, options?: InvokeOptions): Promise<RunResult> {
      return bot._invoke(event, agent, options);
    },

    async stream(agent: string, options?: InvokeOptions): Promise<RunChunk[]> {
      return bot._stream(event, agent, options);
    },

    async reply(text: string): Promise<void> {
      return bot._reply(event, text);
    },
  };

  return event;
}

/**
 * Wraps a synchronous napi call in a Promise that yields to the event loop
 * via setImmediate before executing.
 */
function deferSync<T>(fn: () => T): Promise<T> {
  return new Promise((resolve, reject) => {
    setImmediate(() => {
      try {
        resolve(fn());
      } catch (err) {
        reject(err);
      }
    });
  });
}

interface RegisteredHandler {
  id: number;
  callback: EventHandler;
}

type RawBodyRequest = Request & { rawBody?: Buffer | string };

/**
 * The main Bot class. Provides an idiomatic Node.js API for registering
 * event handlers and processing webhooks from Slack, Teams, and other platforms.
 */
export class Bot {
  private readonly config: BotConfig;
  private readonly registry: InstanceType<typeof native.HandlerRegistry>;
  private readonly handlers: Map<number, RegisteredHandler> = new Map();
  constructor(config: BotConfig) {
    assertNativeAvailable();
    this.config = config;
    this.registry = new native.HandlerRegistry();
  }

  // ---------------------------------------------------------------------------
  // Handler registration
  // ---------------------------------------------------------------------------

  /** Register a handler for @-mention events. */
  mention(handler: EventHandler, options?: HandlerOptions): number {
    return this._register('mention', undefined, undefined, options, handler);
  }

  /** Register a handler for plain message events. */
  message(handler: EventHandler, options?: HandlerOptions): number {
    return this._register('message', undefined, undefined, options, handler);
  }

  /** Register a handler for a slash command. */
  command(
    name: string,
    handler: EventHandler,
    options?: HandlerOptions,
  ): number {
    return this._register('command', name, undefined, options, handler);
  }

  /** Register a handler for a specific emoji reaction. */
  reaction(
    emoji: string,
    handler: EventHandler,
    options?: HandlerOptions,
  ): number {
    return this._register('reaction', undefined, emoji, options, handler);
  }

  /** Register a handler for a raw platform event type or a broad event kind. */
  on(eventType: EventKind | string, handler: EventHandler, options?: HandlerOptions): number {
    if (
      eventType === 'message'
      || eventType === 'mention'
      || eventType === 'command'
      || eventType === 'reaction'
      || eventType === 'raw'
    ) {
      return this._register(eventType, undefined, undefined, options, handler);
    }
    return this._register('raw', undefined, undefined, options, handler, eventType);
  }

  /** Remove a previously registered handler. Returns true if it existed. */
  off(handlerId: number): boolean {
    this.handlers.delete(handlerId);
    return this.registry.unregister(handlerId);
  }

  // ---------------------------------------------------------------------------
  // Webhook handlers
  // ---------------------------------------------------------------------------

  /**
   * Express-compatible request handler for Slack webhooks.
   * Verifies the signature, parses the payload, and dispatches to matching handlers.
   */
  async handleSlackWebhook(req: Request, res: Response): Promise<void> {
    const signingSecret = this.config.slack?.signingSecret;
    if (!signingSecret) {
      res.status(500).json({ error: 'Slack signing secret not configured' });
      return;
    }

    let rawBody: string;
    try {
      rawBody = await getRawBody(req, Boolean(signingSecret));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Invalid request body';
      res.status(400).json({ error: message });
      return;
    }
    const timestamp = (req.headers['x-slack-request-timestamp'] as string) ?? '';
    const signature = (req.headers['x-slack-signature'] as string) ?? '';

    const contentType = (req.headers['content-type'] as string) ?? 'application/json';
    const parsed = await deferSync(() =>
      this.registry.processSlackWebhook(
        Buffer.from(rawBody),
        contentType,
        signingSecret,
        timestamp,
        signature,
      ),
    );

    if (parsed.type === 'rejected') {
      res.status(parsed.status_code ?? 400).json({ error: parsed.error ?? 'Rejected' });
      return;
    }

    if (parsed.type === 'challenge') {
      res.status(200).json({ challenge: parsed.challenge });
      return;
    }

    if (parsed.type === 'ignored' || !parsed.event) {
      res.status(200).json({ ok: true });
      return;
    }

    // Acknowledge immediately
    res.status(200).json({ ok: true });

    const event = toEvent(parsed.event, this);
    await this._dispatch(event, parsed.handler_ids as number[] | undefined);
  }

  /**
   * Express-compatible request handler for Teams webhooks.
   */
  async handleTeamsWebhook(req: Request, res: Response): Promise<void> {
    const body = await getRawBody(req as RawBodyRequest);
    const parsed = await deferSync(() =>
      this.registry.processTeamsWebhook(Buffer.from(body)),
    );

    if (parsed.type === 'rejected') {
      res.status(parsed.status_code ?? 400).json({ error: parsed.error ?? 'Rejected' });
      return;
    }

    if (parsed.type === 'ignored' || !parsed.event) {
      res.status(200).json({ ok: true });
      return;
    }

    res.status(200).json({ ok: true });

    const event = toEvent(parsed.event, this);
    await this._dispatch(event, parsed.handler_ids as number[] | undefined);
  }

  /**
   * Returns an Express Router with webhook routes mounted.
   * @param prefix - Optional route prefix (default: '/').
   */
  expressMiddleware(prefix?: string): Router {
    // Dynamic import to keep express optional
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const express = require('express');
    const router: Router = express.Router();
    const base = prefix ? prefix.replace(/\/+$/, '') : '';
    const slackBodyParser = express.raw({
      type: () => true,
      verify: (req: RawBodyRequest, _res: Response, buf: Buffer) => {
        req.rawBody = Buffer.from(buf);
      },
    });
    const teamsBodyParser = express.raw({
      type: () => true,
      verify: (req: RawBodyRequest, _res: Response, buf: Buffer) => {
        req.rawBody = Buffer.from(buf);
      },
    });

    router.post(`${base}/slack/events`, slackBodyParser, (req, res) => {
      this.handleSlackWebhook(req, res).catch((err) => {
        console.error('[botmux] Slack webhook error:', err);
        if (!res.headersSent) {
          res.status(500).json({ error: 'Internal error' });
        }
      });
    });

    router.post(`${base}/teams/events`, teamsBodyParser, (req, res) => {
      this.handleTeamsWebhook(req, res).catch((err) => {
        console.error('[botmux] Teams webhook error:', err);
        if (!res.headersSent) {
          res.status(500).json({ error: 'Internal error' });
        }
      });
    });

    return router;
  }

  /**
   * Start an Express server listening on the given port with webhook routes.
   */
  listen(port: number): void {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const express = require('express');
    const app = express();
    app.use(this.expressMiddleware());
    app.listen(port, () => {
      console.log(`[botmux] Bot listening on port ${port}`);
    });
  }

  // ---------------------------------------------------------------------------
  // Internal: LangGraph integration (to be implemented with native fetch)
  // ---------------------------------------------------------------------------

  /** @internal */
  async _invoke(
    _event: Event,
    _agent: string,
    _options?: InvokeOptions,
  ): Promise<RunResult> {
    throw new Error(
      'LangGraph client not yet implemented in TypeScript SDK. Use fetch directly.',
    );
  }

  /** @internal */
  async _stream(
    _event: Event,
    _agent: string,
    _options?: InvokeOptions,
  ): Promise<RunChunk[]> {
    throw new Error(
      'LangGraph client not yet implemented in TypeScript SDK. Use fetch directly.',
    );
  }

  /** @internal */
  async _reply(event: Event, text: string): Promise<void> {
    // Platform-specific reply logic. Full implementation requires platform API
    // tokens and HTTP calls. For now, log the reply.
    console.log(
      `[botmux] Reply to ${event.platform.name}/${event.channelId}: ${text}`,
    );
  }

  // ---------------------------------------------------------------------------
  // Internal: registration and dispatch
  // ---------------------------------------------------------------------------

  /** @internal */
  private _register(
    eventKind: EventKind,
    command: string | undefined,
    emoji: string | undefined,
    options: HandlerOptions | undefined,
    callback: EventHandler,
    rawEventType?: string,
  ): number {
    const id: number = this.registry.register(
      eventKind,
      command ?? null,
      options?.pattern ?? null,
      emoji ?? null,
      options?.platform ?? null,
      rawEventType ?? null,
    );
    this.handlers.set(id, { id, callback });
    return id;
  }

  /** @internal */
  private async _dispatch(event: Event, matchedIds?: number[]): Promise<void> {
    // Convert event back to snake_case JSON for the napi registry matcher
    const eventJson = {
      kind: event.kind,
      platform: {
        name: event.platform.name,
        ephemeral: event.platform.ephemeral,
        threads: event.platform.threads,
        reactions: event.platform.reactions,
        streaming: event.platform.streaming,
        modals: event.platform.modals,
        typing_indicator: event.platform.typingIndicator,
      },
      workspace_id: event.workspaceId,
      channel_id: event.channelId,
      thread_id: event.threadId,
      message_id: event.messageId,
      user: event.user,
      text: event.text,
      command: event.command,
      emoji: event.emoji,
      raw_event_type: event.rawEventType,
      raw: event.raw ?? null,
    };

    const resolvedIds = matchedIds ?? await deferSync(() =>
      this.registry.matchEvent(eventJson),
    );

    const promises: Promise<void>[] = [];
    for (const id of resolvedIds) {
      const registered = this.handlers.get(id);
      if (registered) {
        promises.push(
          new Promise<void>((resolve) => resolve(registered.callback(event) as any)).catch((err) => {
            console.error(`[botmux] Handler ${id} threw:`, err);
          }),
        );
      }
    }

    await Promise.all(promises);
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function extractText(result: any): string {
  const messages = result?.output?.messages ?? result?.messages;
  if (Array.isArray(messages) && messages.length > 0) {
    const last = messages[messages.length - 1];
    if (typeof last?.content === 'string') {
      return last.content;
    }
  }
  return '';
}

async function getRawBody(req: RawBodyRequest, requireOriginalBody = false): Promise<string> {
  if (Buffer.isBuffer(req.rawBody)) {
    return req.rawBody.toString('utf-8');
  }
  if (typeof req.rawBody === 'string') {
    return req.rawBody;
  }
  // If body is already parsed as a Buffer or string
  if (Buffer.isBuffer(req.body)) {
    return req.body.toString('utf-8');
  }
  if (typeof req.body === 'string') {
    return req.body;
  }
  if (req.body && typeof req.body === 'object') {
    if (requireOriginalBody) {
      throw new Error(
        'Slack signature verification requires the original raw request body. Use bot.expressMiddleware() or provide req.rawBody.',
      );
    }
    return JSON.stringify(req.body);
  }
  // Read from stream
  return new Promise<string>((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')));
    req.on('error', reject);
  });
}
