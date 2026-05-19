export type DocxPreviewBlock =
  | {
    kind: 'paragraph';
    text: string;
  }
  | {
    kind: 'table';
    rows: string[][];
  };

export interface DocxPreview {
  blocks: DocxPreviewBlock[];
}

interface ZipEntry {
  name: string;
  compressionMethod: number;
  compressedSize: number;
  localHeaderOffset: number;
}

const textDecoder = new TextDecoder('utf-8');

function readUint16LE(bytes: Uint8Array, offset: number): number {
  return bytes[offset] | (bytes[offset + 1] << 8);
}

function readUint32LE(bytes: Uint8Array, offset: number): number {
  return (
    bytes[offset]
    | (bytes[offset + 1] << 8)
    | (bytes[offset + 2] << 16)
    | (bytes[offset + 3] << 24)
  ) >>> 0;
}

function decodeBase64Bytes(contentBase64: string): Uint8Array {
  const binary = window.atob(contentBase64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function findEndOfCentralDirectory(bytes: Uint8Array): number {
  const signature = 0x06054b50;
  const minOffset = Math.max(0, bytes.length - 22 - 0xffff);
  for (let offset = bytes.length - 22; offset >= minOffset; offset -= 1) {
    if (readUint32LE(bytes, offset) === signature) {
      return offset;
    }
  }
  throw new Error('Invalid DOCX package: end of central directory not found.');
}

function readZipEntries(bytes: Uint8Array): ZipEntry[] {
  const eocdOffset = findEndOfCentralDirectory(bytes);
  const entryCount = readUint16LE(bytes, eocdOffset + 10);
  const centralDirectoryOffset = readUint32LE(bytes, eocdOffset + 16);
  const entries: ZipEntry[] = [];
  let offset = centralDirectoryOffset;

  for (let index = 0; index < entryCount; index += 1) {
    if (readUint32LE(bytes, offset) !== 0x02014b50) {
      throw new Error('Invalid DOCX package: central directory is broken.');
    }

    const compressionMethod = readUint16LE(bytes, offset + 10);
    const compressedSize = readUint32LE(bytes, offset + 20);
    const fileNameLength = readUint16LE(bytes, offset + 28);
    const extraLength = readUint16LE(bytes, offset + 30);
    const commentLength = readUint16LE(bytes, offset + 32);
    const localHeaderOffset = readUint32LE(bytes, offset + 42);
    if (compressedSize === 0xffffffff || localHeaderOffset === 0xffffffff) {
      throw new Error('Zip64 DOCX files are not supported for preview yet.');
    }

    const nameStart = offset + 46;
    const name = textDecoder.decode(bytes.subarray(nameStart, nameStart + fileNameLength));
    entries.push({
      name,
      compressionMethod,
      compressedSize,
      localHeaderOffset,
    });
    offset = nameStart + fileNameLength + extraLength + commentLength;
  }

  return entries;
}

async function inflateRaw(compressed: Uint8Array): Promise<Uint8Array> {
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('This browser does not support DOCX decompression.');
  }

  const compressedBytes = compressed.buffer.slice(
    compressed.byteOffset,
    compressed.byteOffset + compressed.byteLength,
  );
  const stream = new Blob([compressedBytes])
    .stream()
    .pipeThrough(new DecompressionStream('deflate-raw' as CompressionFormat));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function readZipEntry(bytes: Uint8Array, entryName: string): Promise<Uint8Array> {
  const entry = readZipEntries(bytes).find((item) => item.name === entryName);
  if (!entry) {
    throw new Error(`DOCX entry not found: ${entryName}`);
  }

  const localOffset = entry.localHeaderOffset;
  if (readUint32LE(bytes, localOffset) !== 0x04034b50) {
    throw new Error('Invalid DOCX package: local file header is broken.');
  }

  const fileNameLength = readUint16LE(bytes, localOffset + 26);
  const extraLength = readUint16LE(bytes, localOffset + 28);
  const dataStart = localOffset + 30 + fileNameLength + extraLength;
  const payload = bytes.subarray(dataStart, dataStart + entry.compressedSize);

  if (entry.compressionMethod === 0) {
    return payload;
  }
  if (entry.compressionMethod === 8) {
    return inflateRaw(payload);
  }
  throw new Error(`Unsupported DOCX compression method: ${entry.compressionMethod}`);
}

function elementName(node: Node): string {
  if (node.nodeType === Node.ELEMENT_NODE) {
    const element = node as Element;
    return element.localName || element.nodeName.split(':').pop() || element.nodeName;
  }
  return node.nodeName.split(':').pop() || node.nodeName;
}

function childElements(element: Element, name: string): Element[] {
  return Array.from(element.childNodes).filter(
    (node): node is Element => node.nodeType === Node.ELEMENT_NODE && elementName(node) === name,
  );
}

function descendantElements(element: Element, name: string): Element[] {
  const result: Element[] = [];
  const visit = (node: Node) => {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === Node.ELEMENT_NODE) {
        const childElement = child as Element;
        if (elementName(childElement) === name) {
          result.push(childElement);
        }
        visit(childElement);
      }
    }
  };
  visit(element);
  return result;
}

function normalizeDocxText(value: string): string {
  return value
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function collectDocxText(node: Node): string {
  let text = '';
  const visit = (current: Node) => {
    for (const child of Array.from(current.childNodes)) {
      if (child.nodeType === Node.TEXT_NODE) {
        continue;
      }
      if (child.nodeType !== Node.ELEMENT_NODE) {
        continue;
      }

      const childElement = child as Element;
      const name = elementName(childElement);
      if (name === 't' || name === 'instrText') {
        text += childElement.textContent ?? '';
        continue;
      }
      if (name === 'tab') {
        text += '\t';
        continue;
      }
      if (name === 'br' || name === 'cr') {
        text += '\n';
        continue;
      }
      visit(childElement);
    }
  };
  visit(node);
  return normalizeDocxText(text);
}

function parseDocxTable(table: Element): DocxPreviewBlock | null {
  const rows = childElements(table, 'tr')
    .map((row) => childElements(row, 'tc')
      .map((cell) => descendantElements(cell, 'p')
        .map((paragraph) => collectDocxText(paragraph))
        .filter(Boolean)
        .join(' ')))
    .filter((row) => row.some((cell) => cell.length > 0));
  return rows.length > 0 ? { kind: 'table', rows } : null;
}

function parseDocumentXml(xmlText: string): DocxPreview {
  const documentXml = new DOMParser().parseFromString(xmlText, 'application/xml');
  if (documentXml.querySelector('parsererror')) {
    throw new Error('DOCX document XML cannot be parsed.');
  }

  const body = descendantElements(documentXml.documentElement, 'body')[0];
  if (!body) {
    throw new Error('DOCX document body not found.');
  }

  const blocks: DocxPreviewBlock[] = [];
  for (const child of Array.from(body.childNodes)) {
    if (child.nodeType !== Node.ELEMENT_NODE) {
      continue;
    }

    const childElement = child as Element;
    const name = elementName(childElement);
    if (name === 'p') {
      const text = collectDocxText(childElement);
      if (text) {
        blocks.push({ kind: 'paragraph', text });
      }
      continue;
    }
    if (name === 'tbl') {
      const table = parseDocxTable(childElement);
      if (table) {
        blocks.push(table);
      }
    }
  }

  return { blocks };
}

export async function extractDocxPreview(contentBase64: string): Promise<DocxPreview> {
  const docxBytes = decodeBase64Bytes(contentBase64);
  const documentXmlBytes = await readZipEntry(docxBytes, 'word/document.xml');
  const documentXmlText = textDecoder.decode(documentXmlBytes);
  return parseDocumentXml(documentXmlText);
}
