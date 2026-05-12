export type AiContextDraft =
  | {
    type: 'folder';
    title: string;
    folderPath: string;
    fileIds: string[];
  }
  | {
    type: 'selected_files';
    title: string;
    fileIds: string[];
  };

