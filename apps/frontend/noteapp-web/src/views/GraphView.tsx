import { useEffect, useMemo, useState } from 'react';
import { Loader2, Network, RefreshCw, Search } from 'lucide-react';

import { useWorkspaceFilesController } from '../useWorkspaceFiles';
import { useWorkspaceLinksController } from '../useWorkspaceLinks';
import type { WorkspaceFileEntry } from '../workspaceFiles';
import type { WorkspaceNoteLink, WorkspaceNoteLinksSnapshot } from '../workspaceLinks';

type GraphNodeKind = 'selected' | 'linked' | 'unresolved';
type GraphEdgeKind = 'outgoing' | 'backlink';

interface GraphNode {
  id: string;
  fileId: string | null;
  label: string;
  path: string;
  kind: GraphNodeKind;
  x: number;
  y: number;
  relationCount: number;
}

interface GraphEdge {
  id: string;
  sourceId: string;
  targetId: string;
  label: string;
  kind: GraphEdgeKind;
}

interface RelationshipRow {
  id: string;
  direction: 'Outgoing' | 'Backlink';
  title: string;
  path: string;
  fileId: string | null;
  unresolved: boolean;
}

function titleFromPath(path: string): string {
  const fileName = path.split(/[\\/]/).pop() || path;
  return fileName.replace(/\.(md|markdown)$/i, '') || path;
}

function isGraphMarkdownFile(file: WorkspaceFileEntry): boolean {
  return (
    file.status === 'active'
    && (
      file.type === 'note'
      || (file.type === 'attachment' && /\.(md|markdown)$/i.test(file.path))
    )
  );
}

function normalizeUnresolvedId(linkText: string): string {
  return linkText.trim().toLocaleLowerCase();
}

function noteNodeId(fileId: string): string {
  return `file:${fileId}`;
}

function unresolvedNodeId(linkText: string): string {
  return `unresolved:${normalizeUnresolvedId(linkText)}`;
}

function clampCoordinate(value: number): number {
  return Math.max(12, Math.min(88, value));
}

function addOrUpdateNode(nodes: Map<string, GraphNode>, node: Omit<GraphNode, 'relationCount'>) {
  const current = nodes.get(node.id);
  if (current) {
    current.relationCount += 1;
    return current;
  }
  const nextNode: GraphNode = { ...node, relationCount: 1 };
  nodes.set(node.id, nextNode);
  return nextNode;
}

function targetNodeForLink(
  link: WorkspaceNoteLink,
  fileById: Map<string, WorkspaceFileEntry>,
): Omit<GraphNode, 'relationCount'> {
  if (link.target_file_id) {
    const targetFile = fileById.get(link.target_file_id);
    const targetPath = link.target_path ?? targetFile?.path ?? link.link_text;
    return {
      id: noteNodeId(link.target_file_id),
      fileId: link.target_file_id,
      label: titleFromPath(targetPath),
      path: targetPath,
      kind: 'linked',
      x: 50,
      y: 50,
    };
  }
  return {
    id: unresolvedNodeId(link.link_text),
    fileId: null,
    label: link.link_text,
    path: 'Unresolved wiki link',
    kind: 'unresolved',
    x: 50,
    y: 50,
  };
}

function sourceNodeForBacklink(
  link: WorkspaceNoteLink,
  fileById: Map<string, WorkspaceFileEntry>,
): Omit<GraphNode, 'relationCount'> {
  const sourceFile = fileById.get(link.source_file_id);
  const sourcePath = sourceFile?.path ?? link.source_path;
  return {
    id: noteNodeId(link.source_file_id),
    fileId: link.source_file_id,
    label: titleFromPath(sourcePath),
    path: sourcePath,
    kind: 'linked',
    x: 50,
    y: 50,
  };
}

function positionGraphNodes(nodes: GraphNode[], selectedNodeId: string): GraphNode[] {
  const selectedNode = nodes.find((node) => node.id === selectedNodeId);
  if (selectedNode) {
    selectedNode.x = 50;
    selectedNode.y = 50;
  }

  const neighbors = nodes.filter((node) => node.id !== selectedNodeId);
  const radiusX = neighbors.length <= 6 ? 30 : 36;
  const radiusY = neighbors.length <= 6 ? 28 : 34;
  neighbors.forEach((node, index) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * index) / Math.max(neighbors.length, 1);
    node.x = clampCoordinate(50 + Math.cos(angle) * radiusX);
    node.y = clampCoordinate(50 + Math.sin(angle) * radiusY);
  });

  return nodes;
}

function buildGraph(
  selectedFile: WorkspaceFileEntry | null,
  links: WorkspaceNoteLinksSnapshot | null,
  fileById: Map<string, WorkspaceFileEntry>,
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  if (!selectedFile) {
    return { nodes: [], edges: [] };
  }

  const selectedNodeId = noteNodeId(selectedFile.file_id);
  const nodes = new Map<string, GraphNode>();
  const edges: GraphEdge[] = [];
  addOrUpdateNode(nodes, {
    id: selectedNodeId,
    fileId: selectedFile.file_id,
    label: titleFromPath(selectedFile.path),
    path: selectedFile.path,
    kind: 'selected',
    x: 50,
    y: 50,
  });

  for (const link of links?.outgoing ?? []) {
    const targetNode = addOrUpdateNode(nodes, targetNodeForLink(link, fileById));
    edges.push({
      id: `outgoing:${link.ordinal}:${targetNode.id}:${link.link_text}`,
      sourceId: selectedNodeId,
      targetId: targetNode.id,
      label: link.link_text,
      kind: 'outgoing',
    });
  }

  for (const link of links?.backlinks ?? []) {
    const sourceNode = addOrUpdateNode(nodes, sourceNodeForBacklink(link, fileById));
    edges.push({
      id: `backlink:${link.source_file_id}:${link.ordinal}:${link.link_text}`,
      sourceId: sourceNode.id,
      targetId: selectedNodeId,
      label: link.link_text,
      kind: 'backlink',
    });
  }

  return {
    nodes: positionGraphNodes(Array.from(nodes.values()), selectedNodeId),
    edges,
  };
}

function buildRelationshipRows(links: WorkspaceNoteLinksSnapshot | null): RelationshipRow[] {
  if (!links) {
    return [];
  }
  return [
    ...links.outgoing.map((link) => ({
      id: `outgoing:${link.ordinal}:${link.target_file_id ?? link.link_text}`,
      direction: 'Outgoing' as const,
      title: link.target_path ? titleFromPath(link.target_path) : link.link_text,
      path: link.target_path ?? 'Unresolved wiki link',
      fileId: link.target_file_id,
      unresolved: !link.target_file_id,
    })),
    ...links.backlinks.map((link) => ({
      id: `backlink:${link.source_file_id}:${link.ordinal}`,
      direction: 'Backlink' as const,
      title: titleFromPath(link.source_path),
      path: link.source_path,
      fileId: link.source_file_id,
      unresolved: false,
    })),
  ];
}

export default function GraphView() {
  const [query, setQuery] = useState('');
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);
  const {
    files,
    source,
    lastError: filesError,
    isRefreshing: isFilesRefreshing,
    refresh,
  } = useWorkspaceFilesController();
  const {
    links,
    lastError: linksError,
    isLoading: isLinksLoading,
    loadLinks,
    clearLinks,
  } = useWorkspaceLinksController();

  const notes = useMemo(
    () => files.filter(isGraphMarkdownFile),
    [files],
  );
  const fileById = useMemo(
    () => new Map(files.map((file) => [file.file_id, file])),
    [files],
  );
  const selectedFile = selectedFileId ? fileById.get(selectedFileId) ?? null : null;
  const currentLinks = links?.file_id === selectedFileId ? links : null;
  const graph = useMemo(
    () => buildGraph(selectedFile, currentLinks, fileById),
    [currentLinks, fileById, selectedFile],
  );
  const relationshipRows = useMemo(() => buildRelationshipRows(currentLinks), [currentLinks]);
  const filteredNotes = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    if (!normalizedQuery) {
      return notes;
    }
    return notes.filter((file) => file.path.toLocaleLowerCase().includes(normalizedQuery));
  }, [notes, query]);
  const edgeById = useMemo(() => {
    const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
    return graph.edges
      .map((edge) => ({
        edge,
        source: nodeById.get(edge.sourceId),
        target: nodeById.get(edge.targetId),
      }))
      .filter((item): item is { edge: GraphEdge; source: GraphNode; target: GraphNode } => Boolean(item.source && item.target));
  }, [graph.edges, graph.nodes]);
  const unresolvedCount = graph.nodes.filter((node) => node.kind === 'unresolved').length;

  useEffect(() => {
    if (notes.length === 0) {
      setSelectedFileId(null);
      return;
    }
    setSelectedFileId((current) => {
      if (current && notes.some((file) => file.file_id === current)) {
        return current;
      }
      return notes[0].file_id;
    });
  }, [notes]);

  useEffect(() => {
    if (!selectedFileId) {
      clearLinks();
      return;
    }
    void loadLinks(selectedFileId);
  }, [clearLinks, loadLinks, selectedFileId]);

  async function refreshGraph() {
    await refresh();
    if (selectedFileId) {
      await loadLinks(selectedFileId);
    }
  }

  function selectGraphNode(node: GraphNode) {
    if (node.fileId) {
      setSelectedFileId(node.fileId);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-[#0d1117] text-[#e3e2e6] lg:flex-row">
      <aside className="flex h-[42vh] min-h-0 w-full flex-col border-b border-[#263247] bg-[#121722] lg:h-full lg:w-96 lg:border-b-0 lg:border-r">
        <div className="border-b border-[#263247] p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border border-[#2b79c2]/40 bg-[#17324d] text-[#8bc8ff]">
                <Network size={18} />
              </div>
              <div className="min-w-0">
                <h2 className="truncate text-[15px] font-bold text-white">Knowledge Graph</h2>
                <p className="mt-0.5 text-[11px] text-slate-500">{source} workspace links</p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void refreshGraph()}
              disabled={isFilesRefreshing || isLinksLoading}
              title="Refresh graph"
              className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[#263247] bg-[#0d1117] text-slate-300 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isFilesRefreshing || isLinksLoading ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
            </button>
          </div>

          <label className="mt-4 flex h-10 items-center gap-2 rounded-lg border border-[#263247] bg-[#0d1117] px-3 text-slate-400">
            <Search size={15} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="min-w-0 flex-1 bg-transparent text-[13px] text-[#e3e2e6] outline-none placeholder:text-slate-600"
              placeholder="Search notes"
            />
          </label>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {filesError && (
            <p className="mb-3 rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] leading-5 text-[#ffb782]">
              {filesError}
            </p>
          )}
          {filteredNotes.length === 0 ? (
            <p className="rounded-lg border border-dashed border-[#263247] p-4 text-[13px] text-slate-500">
              No active notes are available for graph rendering.
            </p>
          ) : (
            <div className="space-y-2">
              {filteredNotes.map((file) => (
                <button
                  type="button"
                  key={file.file_id}
                  onClick={() => setSelectedFileId(file.file_id)}
                  className={`w-full rounded-lg border p-3 text-left transition-colors ${
                    file.file_id === selectedFileId
                      ? 'border-[#2b79c2] bg-[#17324d] text-white'
                      : 'border-[#263247] bg-[#151b26] text-slate-300 hover:border-[#355173] hover:text-white'
                  }`}
                >
                  <span className="block truncate text-[13px] font-semibold">{titleFromPath(file.path)}</span>
                  <span className="mt-1 block truncate font-mono text-[11px] text-slate-500">{file.path}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </aside>

      <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <header className="border-b border-[#263247] bg-[#111827] px-5 py-4">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Selected note</p>
              <h1 className="mt-1 truncate text-xl font-black text-white">
                {selectedFile ? titleFromPath(selectedFile.path) : 'No note selected'}
              </h1>
              {selectedFile && (
                <p className="mt-1 truncate font-mono text-[11px] text-slate-500">{selectedFile.path}</p>
              )}
            </div>
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-lg border border-[#263247] bg-[#0d1117] px-3 py-2">
                <div className="text-base font-black text-white">{graph.nodes.length}</div>
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Nodes</div>
              </div>
              <div className="rounded-lg border border-[#263247] bg-[#0d1117] px-3 py-2">
                <div className="text-base font-black text-white">{graph.edges.length}</div>
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Edges</div>
              </div>
              <div className="rounded-lg border border-[#263247] bg-[#0d1117] px-3 py-2">
                <div className="text-base font-black text-white">{unresolvedCount}</div>
                <div className="text-[10px] uppercase tracking-wider text-slate-500">Open</div>
              </div>
            </div>
          </div>
          {linksError && (
            <p className="mt-3 rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] leading-5 text-[#ffb782]">
              {linksError}
            </p>
          )}
        </header>

        <section className="grid min-h-0 flex-1 grid-rows-[minmax(280px,1fr)_minmax(160px,36%)] overflow-hidden xl:grid-cols-[minmax(0,1fr)_360px] xl:grid-rows-1">
          <div className="relative min-h-0 overflow-hidden bg-[#0b1020]">
            <div className="absolute inset-0 opacity-[0.08]" style={{ backgroundImage: 'radial-gradient(#8bc8ff 1px, transparent 1px)', backgroundSize: '24px 24px' }} />
            {isLinksLoading && (
              <div className="absolute left-4 top-4 z-20 inline-flex items-center gap-2 rounded-lg border border-[#263247] bg-[#111827]/90 px-3 py-2 text-[12px] text-slate-300 shadow-lg">
                <Loader2 size={14} className="animate-spin text-[#8bc8ff]" />
                Loading links
              </div>
            )}
            {graph.nodes.length === 0 ? (
              <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-[13px] text-slate-500">
                Select an active note to load its outgoing links and backlinks.
              </div>
            ) : (
              <>
                <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
                  {edgeById.map(({ edge, source, target }) => (
                    <line
                      key={edge.id}
                      x1={source.x}
                      y1={source.y}
                      x2={target.x}
                      y2={target.y}
                      stroke={edge.kind === 'outgoing' ? '#2b79c2' : '#a78bfa'}
                      strokeOpacity="0.75"
                      strokeWidth={edge.kind === 'outgoing' ? 0.55 : 0.42}
                      strokeDasharray={edge.kind === 'backlink' ? '1.5 1.5' : undefined}
                    />
                  ))}
                </svg>
                {graph.nodes.map((node) => (
                  <button
                    type="button"
                    key={node.id}
                    onClick={() => selectGraphNode(node)}
                    disabled={!node.fileId}
                    className={`absolute z-10 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-2 text-center ${
                      node.fileId ? 'cursor-pointer' : 'cursor-default'
                    }`}
                    style={{ left: `${node.x}%`, top: `${node.y}%` }}
                    title={node.path}
                  >
                    <span
                      className={`block rounded-full border shadow-lg ${
                        node.kind === 'selected'
                          ? 'h-5 w-5 border-[#8bc8ff] bg-[#2b79c2] shadow-[#2b79c2]/50 ring-4 ring-[#2b79c2]/20'
                          : node.kind === 'unresolved'
                            ? 'h-3.5 w-3.5 border-[#ffb782]/70 bg-[#ffb782]/20 shadow-[#ffb782]/20'
                            : 'h-4 w-4 border-[#a78bfa]/70 bg-[#7c3aed] shadow-[#7c3aed]/30'
                      }`}
                    />
                    <span
                      className={`max-w-40 rounded-md border px-2 py-1 text-[11px] font-semibold leading-4 shadow-lg ${
                        node.kind === 'selected'
                          ? 'border-[#2b79c2]/60 bg-[#111827]/95 text-white'
                          : node.kind === 'unresolved'
                            ? 'border-[#ffb782]/30 bg-[#111827]/90 text-[#ffca9f]'
                            : 'border-[#263247] bg-[#111827]/90 text-slate-200'
                      }`}
                    >
                      <span className="line-clamp-2">{node.label}</span>
                    </span>
                  </button>
                ))}
              </>
            )}
          </div>

          <aside className="min-h-0 overflow-y-auto border-t border-[#263247] bg-[#111827] p-4 xl:border-l xl:border-t-0">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-[14px] font-bold text-white">Relationships</h2>
                <p className="mt-1 text-[11px] text-slate-500">
                  {currentLinks ? `${currentLinks.outgoing_count} outgoing, ${currentLinks.backlink_count} backlinks` : 'No links loaded'}
                </p>
              </div>
            </div>

            {relationshipRows.length === 0 ? (
              <p className="mt-4 rounded-lg border border-dashed border-[#263247] p-4 text-[13px] leading-5 text-slate-500">
                This note has no outgoing links or backlinks yet.
              </p>
            ) : (
              <div className="mt-4 space-y-2">
                {relationshipRows.map((row) => (
                  <button
                    type="button"
                    key={row.id}
                    disabled={!row.fileId}
                    onClick={() => row.fileId && setSelectedFileId(row.fileId)}
                    className={`w-full rounded-lg border p-3 text-left ${
                      row.fileId
                        ? 'border-[#263247] bg-[#0d1117] hover:border-[#355173] hover:text-white'
                        : 'cursor-default border-[#ffb782]/20 bg-[#2d2118]/40'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className={`text-[10px] font-bold uppercase tracking-wider ${
                        row.direction === 'Outgoing' ? 'text-[#8bc8ff]' : 'text-[#c4b5fd]'
                      }`}>
                        {row.direction}
                      </span>
                      {row.unresolved && (
                        <span className="rounded border border-[#ffb782]/30 px-1.5 py-0.5 text-[10px] text-[#ffca9f]">
                          unresolved
                        </span>
                      )}
                    </div>
                    <p className="mt-2 truncate text-[13px] font-semibold text-[#e3e2e6]">{row.title}</p>
                    <p className="mt-1 truncate font-mono text-[11px] text-slate-500">{row.path}</p>
                  </button>
                ))}
              </div>
            )}
          </aside>
        </section>
      </main>
    </div>
  );
}
