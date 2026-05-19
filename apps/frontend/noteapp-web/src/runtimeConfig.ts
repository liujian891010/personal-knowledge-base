export type AiModelOption = {
  label: string;
  providerApi: string;
  baseUrl: string;
  modelId: string;
  environment: string;
};

export const defaultAiModelOptions: AiModelOption[] = [
  {
    label: 'GLM-5 CodingPlan',
    providerApi: 'openai-completions',
    baseUrl: 'https://sg-al-cwork-web.mediportal.com.cn/filegpt/ai_router/nologin/xg_ai/',
    modelId: 'glm-5_codingplan',
    environment: '生产',
  },
  {
    label: 'MiniMax M2.7 Highspeed CodingPlan',
    providerApi: 'anthropic-messages',
    baseUrl: 'https://sg-al-cwork-web.mediportal.com.cn/filegpt/ai_router/nologin/xg_claude/',
    modelId: 'MiniMax-M2.7-highspeed_codingplan',
    environment: '生产',
  },
  {
    label: 'Gemini 3.1 Flash Lite Preview',
    providerApi: 'google-generative-ai',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_genai/',
    modelId: 'gemini-3.1-flash-lite-preview',
    environment: '开发',
  },
  {
    label: 'Gemini 3 Flash Preview',
    providerApi: 'google-generative-ai',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_genai/',
    modelId: 'gemini-3-flash-preview',
    environment: '开发',
  },
  {
    label: 'Doubao Seed 1.8',
    providerApi: 'openai-completions',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_ai/',
    modelId: 'doubao-seed-1-8-251215',
    environment: '开发',
  },
  {
    label: 'Kimi K2.5',
    providerApi: 'openai-completions',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_ai/',
    modelId: 'kimi-k2.5',
    environment: '开发',
  },
  {
    label: 'Doubao Seed 2.0 Pro',
    providerApi: 'openai-completions',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_ai/',
    modelId: 'doubao-seed-2-0-pro-260215',
    environment: '开发',
  },
  {
    label: 'GLM-5',
    providerApi: 'openai-completions',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_ai/',
    modelId: 'glm-5',
    environment: '开发',
  },
  {
    label: 'GLM-5 CodingPlan',
    providerApi: 'openai-completions',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_ai/',
    modelId: 'glm-5_codingplan',
    environment: '开发',
  },
  {
    label: 'GPT 5.4 Mini',
    providerApi: 'openai-completions',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_ai/',
    modelId: 'gpt-5.4-mini',
    environment: '开发',
  },
  {
    label: 'Mimo V2 Pro',
    providerApi: 'openai-completions',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_ai/',
    modelId: 'mimo-v2-pro',
    environment: '开发',
  },
  {
    label: 'Mimo V2 Omni',
    providerApi: 'openai-completions',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_ai/',
    modelId: 'mimo-v2-omni',
    environment: '开发',
  },
  {
    label: 'MiniMax M2.5',
    providerApi: 'anthropic-messages',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_claude/',
    modelId: 'MiniMax-M2.5',
    environment: '开发',
  },
  {
    label: 'MiniMax M2.7 Highspeed',
    providerApi: 'anthropic-messages',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_claude/',
    modelId: 'MiniMax-M2.7-highspeed',
    environment: '开发',
  },
  {
    label: 'MiniMax M2.7 Highspeed CodingPlan',
    providerApi: 'anthropic-messages',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_claude/',
    modelId: 'MiniMax-M2.7-highspeed_codingplan',
    environment: '开发',
  },
  {
    label: 'MiniMax M2.7',
    providerApi: 'anthropic-messages',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_claude/',
    modelId: 'MiniMax-M2.7',
    environment: '开发',
  },
  {
    label: 'Claude Opus 4.6',
    providerApi: 'anthropic-messages',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_claude/',
    modelId: 'claude-opus-4-6',
    environment: '开发',
  },
  {
    label: 'Claude Sonnet 4.6',
    providerApi: 'anthropic-messages',
    baseUrl: 'https://cwork-api-test.xgjktech.com.cn/filegpt/ai_router/nologin/xgdev_claude/',
    modelId: 'claude-sonnet-4-6',
    environment: '开发',
  },
];

export function aiModelOptionKey(option: AiModelOption): string {
  return `${option.providerApi}|${option.baseUrl}|${option.modelId}`;
}

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

export function normalizeAiModelOptions(value: unknown): AiModelOption[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter(isAiModelOption)
    .map((option) => ({
      label: option.label.trim(),
      providerApi: option.providerApi.trim(),
      baseUrl: option.baseUrl.trim(),
      modelId: option.modelId.trim(),
      environment: option.environment.trim() || 'Custom',
    }));
}

export function mergeAiModelOptions(options: AiModelOption[]): AiModelOption[] {
  const result = new Map<string, AiModelOption>();
  for (const option of options) {
    result.set(aiModelOptionKey(option), option);
  }
  return Array.from(result.values());
}

function readAiModelOptions(value: string | undefined): AiModelOption[] {
  if (!value?.trim()) {
    return defaultAiModelOptions;
  }
  try {
    const options = normalizeAiModelOptions(JSON.parse(value));
    return options.length > 0 ? options : defaultAiModelOptions;
  } catch {
    return defaultAiModelOptions;
  }
}

export const loginCheckUrl = (import.meta.env.VITE_NOTEAPP_LOGIN_CHECK_URL || '').trim();

export const aiModelOptions = readAiModelOptions(import.meta.env.VITE_NOTEAPP_AI_MODEL_OPTIONS_JSON);
