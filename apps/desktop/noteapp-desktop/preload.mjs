import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('noteappDesktop', {
  isDesktop: true,
  platform: process.platform,
  selectWorkspaceFolder: () => ipcRenderer.invoke('noteapp:select-workspace-folder'),
});
