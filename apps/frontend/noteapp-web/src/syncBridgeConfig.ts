const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';

export const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');
