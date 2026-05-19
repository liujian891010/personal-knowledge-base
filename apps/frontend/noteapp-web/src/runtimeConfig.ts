export type AiModelOption = {
  label: string;
  providerApi: string;
  baseUrl: string;
  modelId: string;
  environment: string;
};

const defaultAiModelOptions: AiModelOption[] = [
  {
    label: 'OpenAI compatible',
    providerApi: 'openai-completions',
    baseUrl: 'https://api.openai.com/v1',
    modelId: 'gpt-4o-mini',
    environment: 'Default',
  },
];

function isAiModelOption(value: unknown): value is AiModelOption {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const option = value as Record<string, unknown>;
  return (
    typeof option.label === 'string'
    && typeof option.providerApi === 'string'
    && typeof option.baseUrl === 'string'
    && typeof option.modelId === 'string'
    && typeof option.environment === 'string'
    && option.label.trim().length > 0
    && option.providerApi.trim().length > 0
    && option.baseUrl.trim().length > 0
    && option.modelId.trim().length > 0
  );
}

function readAiModelOptions(value: string | undefined): AiModelOption[] {
  if (!value?.trim()) {
    return defaultAiModelOptions;
  }
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) {
      return defaultAiModelOptions;
    }
    const options = parsed.filter(isAiModelOption).map((option) => ({
      label: option.label.trim(),
      providerApi: option.providerApi.trim(),
      baseUrl: option.baseUrl.trim(),
      modelId: option.modelId.trim(),
      environment: option.environment.trim() || 'Custom',
    }));
    return options.length > 0 ? options : defaultAiModelOptions;
  } catch {
    return defaultAiModelOptions;
  }
}

export const loginCheckUrl = (import.meta.env.VITE_NOTEAPP_LOGIN_CHECK_URL || '').trim();

export const aiModelOptions = readAiModelOptions(import.meta.env.VITE_NOTEAPP_AI_MODEL_OPTIONS_JSON);
