export interface NoteappDesktopApi {
  isDesktop: boolean;
  platform: string;
  selectWorkspaceFolder: () => Promise<{ canceled: boolean; path: string | null }>;
}

declare global {
  interface Window {
    noteappDesktop?: NoteappDesktopApi;
  }
}

export function getDesktopApi(): NoteappDesktopApi | null {
  if (typeof window === 'undefined') {
    return null;
  }
  const api = window.noteappDesktop;
  if (!api || api.isDesktop !== true || typeof api.selectWorkspaceFolder !== 'function') {
    return null;
  }
  return api;
}

export async function selectDesktopWorkspaceFolder(): Promise<{ canceled: boolean; path: string | null } | null> {
  const api = getDesktopApi();
  if (!api) {
    return null;
  }
  return api.selectWorkspaceFolder();
}
