import type { FamiliarApi } from '../preload/index';

declare global {
  interface Window {
    familiar: FamiliarApi;
  }
}
export {};
