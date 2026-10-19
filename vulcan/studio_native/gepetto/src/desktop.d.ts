export {};
declare global {
  interface Window {
    sculptorsHoardDesktop?: {
      pickBlend: () => Promise<string | null>;
      pickPortable?: (folder?: boolean) => Promise<string | null>;
      savePortable?: () => Promise<string | null>;
    };
  }
}
