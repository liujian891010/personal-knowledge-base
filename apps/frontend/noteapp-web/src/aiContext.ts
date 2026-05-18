export type AiContextDraft =
  | {
    type: 'folder';
    title: string;
    folderPath: string;
    fileIds: string[];
    initialInstruction?: string;
    autoRun?: boolean;
  }
  | {
    type: 'selected_files';
    title: string;
    fileIds: string[];
    initialInstruction?: string;
    autoRun?: boolean;
  };
