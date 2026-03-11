import { bench } from 'vitest';
import { Bot, setNativeModule } from '../src/bot.js';
import type { BotConfig } from '../src/types.js';

let nextHandlerId = 1;
let matchedIds: number[] = [];

const slackEvent = {
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
  user: { id: 'U789', name: 'benchuser' },
  text: 'hello benchmark',
  raw: {},
};

class HandlerRegistry {
  register(): number {
    return nextHandlerId++;
  }

  unregister(): boolean {
    return true;
  }

  matchEvent(): number[] {
    return matchedIds;
  }

  processSlackWebhook(
    _body: Buffer,
    _contentType: string,
    _signingSecret: string,
    _timestamp: string,
    _signature: string,
  ): Record<string, unknown> {
    return { type: 'dispatch', event: slackEvent, handler_ids: matchedIds };
  }

  processTeamsWebhook(): Record<string, unknown> {
    return { type: 'ignored' };
  }
}

class LangGraphClient {
  createRun(): string {
    return 'run-1';
  }

  waitRun(): Record<string, unknown> {
    return { id: 'run-1', status: 'completed', output: { messages: [{ content: 'ok' }] } };
  }

  streamNewRun(): Array<Record<string, unknown>> {
    return [];
  }

  cancelRun(): void {}
}

setNativeModule({
  HandlerRegistry,
  LangGraphClient,
  slackVerifySignature: () => true,
  slackParseWebhook: () => ({ type: 'event', event: slackEvent }),
  slackStripMentions: (text: string) => text.replace(/<@[^>]+>/g, '').trim(),
  teamsParseWebhook: () => null,
  teamsStripMentions: (text: string) => text,
  deterministicThreadId: (
    platform: string,
    workspace: string,
    channel: string,
    thread: string,
  ) => `${platform}:${workspace}:${channel}:${thread}`,
});

function makeConfig(): BotConfig {
  return {
    slack: { signingSecret: 'test-secret', botToken: 'xoxb-test' },
    langGraph: { url: 'http://localhost:8123' },
  };
}

function makeResponse(): any {
  return {
    headersSent: false,
    statusCode: 0,
    body: undefined,
    status(code: number) {
      this.statusCode = code;
      return this;
    },
    json(data: unknown) {
      this.body = data;
      this.headersSent = true;
      return this;
    },
  };
}

function makeSlackRequest(body: Buffer): any {
  return {
    headers: {
      'content-type': 'application/json',
      'x-slack-request-timestamp': '1234567890',
      'x-slack-signature': 'v0=abc123',
    },
    body,
  };
}

function buildDispatchBot(handlerCount = 64): { bot: Bot; event: any } {
  nextHandlerId = 1;
  matchedIds = [];

  const bot = new Bot(makeConfig());
  for (let i = 0; i < handlerCount; i += 1) {
    bot.message(async () => undefined, { pattern: `token-${i}` });
  }
  const targetId = bot.mention(async () => undefined, { pattern: 'benchmark' });
  matchedIds = [targetId];

  return {
    bot,
    event: {
      kind: 'mention',
      platform: {
        name: 'slack',
        ephemeral: true,
        threads: true,
        reactions: true,
        streaming: true,
        modals: true,
        typingIndicator: true,
      },
      workspaceId: 'T123',
      channelId: 'C456',
      threadId: 'thread1',
      messageId: 'msg1',
      user: { id: 'U789', name: 'benchuser' },
      text: 'hello benchmark',
      raw: {},
    },
  };
}

const dispatchFixture = buildDispatchBot();
const slackBody = Buffer.from(JSON.stringify({ event: { type: 'app_mention' } }), 'utf-8');

nextHandlerId = 1;
matchedIds = [];
const webhookBot = new Bot(makeConfig());
const webhookHandlerId = webhookBot.mention(async () => undefined);
matchedIds = [webhookHandlerId];

bench('dispatch matched event through registry bridge', async () => {
  await (dispatchFixture.bot as any)._dispatch(dispatchFixture.event);
});

bench('handle slack webhook end-to-end', async () => {
  const req = makeSlackRequest(slackBody);
  const res = makeResponse();
  await webhookBot.handleSlackWebhook(req, res);
  if (res.statusCode !== 200) {
    throw new Error(`unexpected status code ${res.statusCode}`);
  }
});
