export type DeepLinkHandler = (
  path: string,
  searchParams: URLSearchParams,
) => boolean;

export interface IDeepLinkRegistry {
  registerHandler(key: string, handler: DeepLinkHandler): void;
  unregisterHandler(key: string): void;
  getProtocol(): string;
  /**
   * Dispatch a deep-link URL through the registered handlers — the same path
   * OS-delivered links take, callable from in-app surfaces without an OS hop.
   */
  handleUrl(url: string): boolean;
}

export const DEEP_LINK_SERVICE = Symbol.for("posthog.platform.deepLink");
