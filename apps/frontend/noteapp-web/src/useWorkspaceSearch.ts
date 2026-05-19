import { useEffect, useState } from 'react';

import {
  parseWorkspaceSearchSnapshot,
  type WorkspaceSearchResult,
} from './workspaceSearch';
import { syncBridgeUrl } from './syncBridgeConfig';

export interface WorkspaceSearchController {
  query: string;
  setQuery: (query: string) => void;
  results: WorkspaceSearchResult[];
  totalCount: number;
  lastError: string | null;
  isSearching: boolean;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isErrorPayload(payload: unknown): payload is { code?: string; message?: string } {
  return typeof payload === 'object' && payload !== null;
}

async function responseErrorMessage(response: Response): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (isErrorPayload(payload) && typeof payload.message === 'string') {
      const code = typeof payload.code === 'string' ? ` (${payload.code})` : '';
      return `workspace search returned ${response.status}${code}: ${payload.message}`;
    }
  } catch {
    // Fall through to the status-only error.
  }
  return `workspace search returned ${response.status}`;
}

async function searchWorkspace(query: string): Promise<WorkspaceSearchResult[]> {
  const response = await fetch(
    `${syncBridgeUrl}/api/workspace/search?q=${encodeURIComponent(query)}&limit=20`,
    { cache: 'no-store' },
  );
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response));
  }
  return parseWorkspaceSearchSnapshot(await response.json()).results;
}

export function useWorkspaceSearchController(): WorkspaceSearchController {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<WorkspaceSearchResult[]>([]);
  const [lastError, setLastError] = useState<string | null>(null);
  const [isSearching, setIsSearching] = useState(false);

  useEffect(() => {
    const normalizedQuery = query.trim();
    if (!normalizedQuery) {
      setResults([]);
      setLastError(null);
      setIsSearching(false);
      return;
    }

    let cancelled = false;
    setIsSearching(true);
    const timer = window.setTimeout(() => {
      searchWorkspace(normalizedQuery)
        .then((nextResults) => {
          if (cancelled) {
            return;
          }
          setResults(nextResults);
          setLastError(null);
        })
        .catch((error) => {
          if (!cancelled) {
            setResults([]);
            setLastError(errorMessage(error));
          }
        })
        .finally(() => {
          if (!cancelled) {
            setIsSearching(false);
          }
        });
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  return {
    query,
    setQuery,
    results,
    totalCount: results.length,
    lastError,
    isSearching,
  };
}
