import { describe, it, expect, vi, beforeEach } from 'vitest';
import { Bot, setNativeModule } from '../src/bot.js';
import type { BotConfig, EventHandler } from '../src/types.js';

// ---------------------------------------------------------------------------
// Mock the native module
// ---------------------------------------------------------------------------

const mockRegistry = {
  register: vi.fn(),
  unregister: vi.fn(),
  matchEvent: vi.fn(),
};

const mockLangGraphClient = {
  createRun: vi.fn(),
  waitRun: vi.fn(),
  streamNewRun: vi.fn(),
  cancelRun: vi.fn(),
};

const mockNative = {
  HandlerRegistry: vi.fn(function(this: any) { Object.assign(this, mockRegistry); return this; }),
  LangGraphClient: vi.fn(function(this: any) { Object.assign(this, mockLangGraphClient); return this; }),
  slackVerifySignature: vi.fn(() => true),
  slackParseWebhook: vi.fn(() => ({
    type: 'event',
    event: {
      kind: 'mention',
      platform: {
        name: 'slack',
        ephemeral: true,
        threads: true,
        reactions: true,
        streaming: true,
        modals: true,
        typing_indicator: true,
      },
      workspace_id: 'T123',
      channel_id: 'C456',
      thread_id: 'thread1',
      message_id: 'msg1',
      user: { id: 'U789', name: 'testuser' },
      text: 'hello bot',
      raw: {},
    },
  })),
  slackStripMentions: vi.fn((text: string) => text.replace(/<@[^>]+>/g, '').trim()),
  teamsParseWebhook: vi.fn(() => ({
    kind: 'message',
    platform: {
      name: 'teams',
      ephemeral: false,
      threads: true,
      reactions: false,
      streaming: false,
      modals: false,
      typing_indicator: true,
    },
    workspace_id: 'tenant1',
    channel_id: 'conv1',
    thread_id: 'thread1',
    message_id: 'msg1',
    user: { id: 'user1', name: 'teamsuser' },
    text: 'hello from teams',
    raw: {},
  })),
  teamsStripMentions: vi.fn((text: string) => text),
  deterministicThreadId: vi.fn(
    (platform: string, workspace: string, channel: string, thread: string) =>
      `${platform}:${workspace}:${channel}:${thread}`,
  ),
  platformCapabilities: vi.fn(),
};

setNativeModule(mockNative);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeConfig(overrides?: Partial<BotConfig>): BotConfig {
  return {
    slack: { signingSecret: 'test-secret', botToken: 'xoxb-test' },
    langGraph: { url: 'http://localhost:8123' },
    ...overrides,
  };
}

function mockRequest(overrides: Record<string, any> = {}): any {
  return {
    headers: {
      'content-type': 'application/json',
      'x-slack-request-timestamp': '1234567890',
      'x-slack-signature': 'v0=abc123',
      ...overrides.headers,
    },
    body: overrides.body ?? JSON.stringify({ event: {} }),
    rawBody: overrides.rawBody,
    on: vi.fn(),
  };
}

function mockResponse(): any {
  const res: any = {
    headersSent: false,
    statusCode: 0,
    body: null,
  };
  res.status = vi.fn((code: number) => {
    res.statusCode = code;
    return res;
  });
  res.json = vi.fn((data: any) => {
    res.body = data;
    res.headersSent = true;
    return res;
  });
  return res;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Bot', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRegistry.register.mockReturnValue(1);
    mockRegistry.matchEvent.mockReturnValue([]);
  });

  describe('creation and configuration', () => {
    it('creates a bot with slack config', () => {
      const bot = new Bot(makeConfig());
      expect(bot).toBeDefined();
    });

    it('creates a bot without langGraph config', () => {
      const bot = new Bot({ slack: { signingSecret: 's', botToken: 't' } });
      expect(bot).toBeDefined();
    });

    it('creates a bot with teams config', () => {
      const bot = new Bot({
        teams: { appId: 'app', appPassword: 'pass' },
      });
      expect(bot).toBeDefined();
    });

    it('creates a bot with minimal config', () => {
      const bot = new Bot({});
      expect(bot).toBeDefined();
    });

    it('fails with an actionable error when native bindings are unavailable', () => {
      setNativeModule(undefined);
      expect(() => new Bot(makeConfig())).toThrow(
        'lsmsg native bindings are unavailable',
      );
      setNativeModule(mockNative);
    });
  });

  describe('handler registration', () => {
    let bot: Bot;

    beforeEach(() => {
      bot = new Bot(makeConfig());
      let nextId = 1;
      mockRegistry.register.mockImplementation(() => nextId++);
    });

    it('registers a mention handler', () => {
      const handler = vi.fn();
      const id = bot.mention(handler);
      expect(id).toBe(1);
      expect(mockRegistry.register).toHaveBeenCalledWith(
        'mention', null, null, null, null, null,
      );
    });

    it('registers a message handler', () => {
      const handler = vi.fn();
      const id = bot.message(handler);
      expect(id).toBe(1);
      expect(mockRegistry.register).toHaveBeenCalledWith(
        'message', null, null, null, null, null,
      );
    });

    it('registers a command handler', () => {
      const handler = vi.fn();
      const id = bot.command('/ask', handler);
      expect(id).toBe(1);
      expect(mockRegistry.register).toHaveBeenCalledWith(
        'command', '/ask', null, null, null, null,
      );
    });

    it('registers a reaction handler', () => {
      const handler = vi.fn();
      const id = bot.reaction('thumbsup', handler);
      expect(id).toBe(1);
      expect(mockRegistry.register).toHaveBeenCalledWith(
        'reaction', null, null, 'thumbsup', null, null,
      );
    });

    it('registers with pattern option', () => {
      const handler = vi.fn();
      bot.mention(handler, { pattern: 'help.*' });
      expect(mockRegistry.register).toHaveBeenCalledWith(
        'mention', null, 'help.*', null, null, null,
      );
    });

    it('registers with platform option', () => {
      const handler = vi.fn();
      bot.message(handler, { platform: 'slack' });
      expect(mockRegistry.register).toHaveBeenCalledWith(
        'message', null, null, null, 'slack', null,
      );
    });

    it('registers with both pattern and platform', () => {
      const handler = vi.fn();
      bot.mention(handler, { pattern: 'deploy', platform: 'slack' });
      expect(mockRegistry.register).toHaveBeenCalledWith(
        'mention', null, 'deploy', null, 'slack', null,
      );
    });

    it('registers via on() for broad event kinds', () => {
      const handler = vi.fn();
      bot.on('raw', handler);
      expect(mockRegistry.register).toHaveBeenCalledWith(
        'raw', null, null, null, null, null,
      );
    });

    it('registers raw event types via on()', () => {
      const handler = vi.fn();
      bot.on('app_home_opened', handler);
      expect(mockRegistry.register).toHaveBeenCalledWith(
        'raw', null, null, null, null, 'app_home_opened',
      );
    });

    it('unregisters a handler with off()', () => {
      mockRegistry.unregister.mockReturnValue(true);
      const handler = vi.fn();
      const id = bot.mention(handler);
      const result = bot.off(id);
      expect(result).toBe(true);
      expect(mockRegistry.unregister).toHaveBeenCalledWith(id);
    });

    it('returns false when unregistering unknown handler', () => {
      mockRegistry.unregister.mockReturnValue(false);
      expect(bot.off(999)).toBe(false);
    });

    it('registers multiple handlers with unique IDs', () => {
      const h1 = vi.fn();
      const h2 = vi.fn();
      const id1 = bot.mention(h1);
      const id2 = bot.message(h2);
      expect(id1).not.toBe(id2);
    });
  });

  describe('Slack webhook handling', () => {
    it('verifies signature and dispatches event', async () => {
      const bot = new Bot(makeConfig());
      const handler = vi.fn();
      mockRegistry.register.mockReturnValue(1);
      bot.mention(handler);
      mockRegistry.matchEvent.mockReturnValue([1]);

      const req = mockRequest({ body: '{"event":{}}' });
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);

      expect(res.statusCode).toBe(200);
      expect(res.body).toEqual({ ok: true });
      // Handler should be called (async, via setImmediate)
      // Wait for the dispatch to complete
      await new Promise((r) => setTimeout(r, 50));
      expect(handler).toHaveBeenCalledTimes(1);
      expect(handler.mock.calls[0][0].kind).toBe('mention');
      expect(handler.mock.calls[0][0].platform.name).toBe('slack');
      expect(handler.mock.calls[0][0].text).toBe('hello bot');
    });

    it('rejects invalid signature', async () => {
      const { slackVerifySignature } = mockNative as any;
      (slackVerifySignature as any).mockReturnValueOnce(false);

      const bot = new Bot(makeConfig());
      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);

      expect(res.statusCode).toBe(401);
      expect(res.body).toEqual({ error: 'Invalid signature' });
    });

    it('responds to URL verification challenge', async () => {
      const { slackParseWebhook } = mockNative as any;
      (slackParseWebhook as any).mockReturnValueOnce({
        type: 'challenge',
        challenge: 'test-challenge-token',
      });

      const bot = new Bot(makeConfig());
      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);

      expect(res.statusCode).toBe(200);
      expect(res.body).toEqual({ challenge: 'test-challenge-token' });
    });

    it('handles ignored events gracefully', async () => {
      const { slackParseWebhook } = mockNative as any;
      (slackParseWebhook as any).mockReturnValueOnce({ type: 'ignored' });

      const bot = new Bot(makeConfig());
      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);

      expect(res.statusCode).toBe(200);
      expect(res.body).toEqual({ ok: true });
    });

    it('returns 500 when slack signing secret is not configured', async () => {
      const bot = new Bot({ langGraph: { url: 'http://localhost:8123' } });
      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);

      expect(res.statusCode).toBe(500);
    });
  });

  describe('Teams webhook handling', () => {
    it('parses and dispatches Teams event', async () => {
      const bot = new Bot({
        teams: { appId: 'app', appPassword: 'pass' },
      });
      const handler = vi.fn();
      mockRegistry.register.mockReturnValue(1);
      bot.message(handler);
      mockRegistry.matchEvent.mockReturnValue([1]);

      const req = mockRequest({ body: { type: 'message', text: 'hi' } });
      const res = mockResponse();

      await bot.handleTeamsWebhook(req, res);

      expect(res.statusCode).toBe(200);
      await new Promise((r) => setTimeout(r, 50));
      expect(handler).toHaveBeenCalledTimes(1);
      expect(handler.mock.calls[0][0].kind).toBe('message');
      expect(handler.mock.calls[0][0].platform.name).toBe('teams');
    });

    it('handles null parse result', async () => {
      const { teamsParseWebhook } = mockNative as any;
      (teamsParseWebhook as any).mockReturnValueOnce(null);

      const bot = new Bot({
        teams: { appId: 'app', appPassword: 'pass' },
      });
      const req = mockRequest({ body: {} });
      const res = mockResponse();

      await bot.handleTeamsWebhook(req, res);

      expect(res.statusCode).toBe(200);
      expect(res.body).toEqual({ ok: true });
    });
  });

  describe('handler dispatch and matching', () => {
    it('dispatches to multiple matching handlers', async () => {
      const bot = new Bot(makeConfig());
      const h1 = vi.fn();
      const h2 = vi.fn();
      mockRegistry.register.mockReturnValueOnce(1).mockReturnValueOnce(2);
      bot.mention(h1);
      bot.on('mention', h2);
      mockRegistry.matchEvent.mockReturnValue([1, 2]);

      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);
      await new Promise((r) => setTimeout(r, 50));

      expect(h1).toHaveBeenCalledTimes(1);
      expect(h2).toHaveBeenCalledTimes(1);
    });

    it('does not call handlers when no match', async () => {
      const bot = new Bot(makeConfig());
      const handler = vi.fn();
      mockRegistry.register.mockReturnValue(1);
      bot.mention(handler);
      mockRegistry.matchEvent.mockReturnValue([]);

      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);
      await new Promise((r) => setTimeout(r, 50));

      expect(handler).not.toHaveBeenCalled();
    });

    it('event has internalThreadId', async () => {
      const bot = new Bot(makeConfig());
      let receivedEvent: any;
      const handler = vi.fn((e) => {
        receivedEvent = e;
      });
      mockRegistry.register.mockReturnValue(1);
      bot.mention(handler);
      mockRegistry.matchEvent.mockReturnValue([1]);

      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);
      await new Promise((r) => setTimeout(r, 50));

      expect(receivedEvent.internalThreadId).toBe('slack:T123:C456:thread1');
    });
  });

  describe('event methods', () => {
    it('event.invoke() calls LangGraph client', async () => {
      mockLangGraphClient.createRun.mockReturnValue('run-1');
      mockLangGraphClient.waitRun.mockReturnValue({
        id: 'run-1',
        status: 'completed',
        output: { messages: [{ role: 'assistant', content: 'Hello!' }] },
      });

      const bot = new Bot(makeConfig());
      let receivedEvent: any;
      const handler = vi.fn(async (e) => {
        receivedEvent = e;
        const result = await e.invoke('my-agent');
        expect(result.id).toBe('run-1');
        expect(result.status).toBe('completed');
        expect(result.text).toBe('Hello!');
      });
      mockRegistry.register.mockReturnValue(1);
      bot.mention(handler);
      mockRegistry.matchEvent.mockReturnValue([1]);

      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);
      await new Promise((r) => setTimeout(r, 100));

      expect(handler).toHaveBeenCalled();
    });

    it('event.stream() calls LangGraph client', async () => {
      mockLangGraphClient.streamNewRun.mockReturnValue([
        { event: 'messages/partial', text: 'He', text_delta: 'He', data: {} },
        { event: 'messages/partial', text: 'Hello', text_delta: 'llo', data: {} },
      ]);

      const bot = new Bot(makeConfig());
      const handler = vi.fn(async (e) => {
        const chunks = await e.stream('my-agent');
        expect(chunks).toHaveLength(2);
        expect(chunks[0].textDelta).toBe('He');
        expect(chunks[1].text).toBe('Hello');
      });
      mockRegistry.register.mockReturnValue(1);
      bot.mention(handler);
      mockRegistry.matchEvent.mockReturnValue([1]);

      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);
      await new Promise((r) => setTimeout(r, 100));

      expect(handler).toHaveBeenCalled();
    });

    it('event.invoke() throws when langGraph not configured', async () => {
      const bot = new Bot({ slack: { signingSecret: 's', botToken: 't' } });
      const handler = vi.fn(async (e) => {
        await expect(e.invoke('agent')).rejects.toThrow('LangGraph client not configured');
      });
      mockRegistry.register.mockReturnValue(1);
      bot.mention(handler);
      mockRegistry.matchEvent.mockReturnValue([1]);

      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);
      await new Promise((r) => setTimeout(r, 100));

      expect(handler).toHaveBeenCalled();
    });

    it('event.reply() does not throw', async () => {
      const bot = new Bot(makeConfig());
      const handler = vi.fn(async (e) => {
        await expect(e.reply('Thanks!')).resolves.toBeUndefined();
      });
      mockRegistry.register.mockReturnValue(1);
      bot.mention(handler);
      mockRegistry.matchEvent.mockReturnValue([1]);

      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);
      await new Promise((r) => setTimeout(r, 50));

      expect(handler).toHaveBeenCalled();
    });
  });

  describe('Express middleware', () => {
    it('expressMiddleware returns an object with post routes', () => {
      // Mock express.Router
      const mockRouter = {
        post: vi.fn(),
      };
      vi.doMock('express', () => ({
        default: Object.assign(vi.fn(() => ({})), {
          Router: vi.fn(() => mockRouter),
          json: vi.fn(),
          raw: vi.fn(),
        }),
        Router: vi.fn(() => mockRouter),
      }));

      // Since express is dynamically required, we test that the bot creates routes
      const bot = new Bot(makeConfig());

      // This will call require('express') internally. In a real environment
      // express would be installed. Here we verify the bot doesn't crash
      // on construction and the method exists.
      expect(typeof bot.expressMiddleware).toBe('function');
      expect(typeof bot.listen).toBe('function');
    });
  });

  describe('error handling', () => {
    it('handler errors are caught and logged', async () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

      const bot = new Bot(makeConfig());
      const handler = vi.fn(() => {
        throw new Error('handler boom');
      });
      mockRegistry.register.mockReturnValue(1);
      bot.mention(handler);
      mockRegistry.matchEvent.mockReturnValue([1]);

      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);
      await new Promise((r) => setTimeout(r, 50));

      expect(handler).toHaveBeenCalled();
      expect(consoleSpy).toHaveBeenCalledWith(
        expect.stringContaining('Handler 1 threw'),
        expect.any(Error),
      );
      consoleSpy.mockRestore();
    });

    it('async handler errors are caught and logged', async () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

      const bot = new Bot(makeConfig());
      const handler = vi.fn(async () => {
        throw new Error('async handler boom');
      });
      mockRegistry.register.mockReturnValue(1);
      bot.mention(handler);
      mockRegistry.matchEvent.mockReturnValue([1]);

      const req = mockRequest();
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);
      await new Promise((r) => setTimeout(r, 50));

      expect(handler).toHaveBeenCalled();
      expect(consoleSpy).toHaveBeenCalled();
      consoleSpy.mockRestore();
    });

    it('handles Buffer request body', async () => {
      const bot = new Bot(makeConfig());
      mockRegistry.matchEvent.mockReturnValue([]);

      const req = mockRequest({ body: Buffer.from('{"event":{}}') });
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);

      expect(res.statusCode).toBe(200);
    });

    it('handles object request body', async () => {
      const bot = new Bot(makeConfig());
      mockRegistry.matchEvent.mockReturnValue([]);

      const req = mockRequest({
        body: { event: {} },
        rawBody: Buffer.from('{"event":{}}'),
      });
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);

      expect(res.statusCode).toBe(200);
    });

    it('rejects object request body without raw bytes when verifying signatures', async () => {
      const bot = new Bot(makeConfig());
      const req = mockRequest({ body: { event: {} } });
      const res = mockResponse();

      await bot.handleSlackWebhook(req, res);

      expect(res.statusCode).toBe(400);
      expect(res.body).toEqual({
        error: 'Slack signature verification requires the original raw request body. Use bot.expressMiddleware() or provide req.rawBody.',
      });
    });
  });
});
