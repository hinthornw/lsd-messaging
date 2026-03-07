/** Supported messaging platforms. */
export type Platform =
  | 'slack'
  | 'teams'
  | 'discord'
  | 'telegram'
  | 'github'
  | 'linear'
  | 'gchat';

/** Kinds of events the bot can handle. */
export type EventKind = 'message' | 'mention' | 'command' | 'reaction' | 'raw';

/** Describes what a platform supports. */
export interface PlatformCapabilities {
  name: Platform;
  ephemeral: boolean;
  threads: boolean;
  reactions: boolean;
  streaming: boolean;
  modals: boolean;
  typingIndicator: boolean;
}

/** Information about the user who triggered an event. */
export interface UserInfo {
  id: string;
  name?: string;
  email?: string;
}

/** A normalized event from any platform. */
export interface Event {
  kind: EventKind;
  platform: PlatformCapabilities;
  workspaceId: string;
  channelId: string;
  threadId: string;
  messageId: string;
  user: UserInfo;
  text: string;
  command?: string;
  emoji?: string;
  rawEventType?: string;
  raw: unknown;
  internalThreadId: string;

  /**
   * Invoke a LangGraph agent synchronously (waits for completion).
   * Returns the run result including extracted text.
   */
  invoke(agent: string, options?: InvokeOptions): Promise<RunResult>;

  /**
   * Stream a LangGraph agent run, returning collected chunks.
   */
  stream(agent: string, options?: InvokeOptions): Promise<RunChunk[]>;

  /**
   * Reply to the event. Platform-specific delivery.
   * Currently logs; full implementation requires platform API tokens.
   */
  reply(text: string): Promise<void>;
}

/** Options for invoke/stream calls. */
export interface InvokeOptions {
  input?: Record<string, unknown>;
  config?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

/** Result of a completed LangGraph run. */
export interface RunResult {
  id: string;
  status: string;
  output: unknown;
  text: string;
}

/** A single chunk from a streaming LangGraph run. */
export interface RunChunk {
  event: string;
  text: string;
  textDelta: string;
  data: Record<string, unknown>;
}

/** Configuration for Slack credentials. */
export interface SlackConfig {
  signingSecret: string;
  botToken: string;
}

/** Configuration for Teams credentials. */
export interface TeamsConfig {
  appId: string;
  appPassword: string;
}

/** Configuration for the LangGraph backend. */
export interface LangGraphConfig {
  url: string;
  apiKey?: string;
}

/** Top-level configuration for creating a Bot. */
export interface BotConfig {
  slack?: SlackConfig;
  teams?: TeamsConfig;
  langGraph?: LangGraphConfig;
}

/** Options for handler registration methods. */
export interface HandlerOptions {
  pattern?: string;
  platform?: Platform;
}

/** An event handler callback. */
export type EventHandler = (event: Event) => Promise<void> | void;
