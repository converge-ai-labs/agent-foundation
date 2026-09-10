export const providerKeyUrls: Record<string, { href: string; label?: string }> =
  {
    openai: { href: "https://platform.openai.com/api-keys" },
    anthropic: { href: "https://platform.claude.com/settings/keys" },
    google_gemini: { href: "https://aistudio.google.com/app/apikey" },
    azure_openai: {
      href: "https://learn.microsoft.com/en-us/azure/ai-foundry/openai/quickstart",
    },
    openrouter: { href: "https://openrouter.ai/settings/keys" },
    deepseek: { href: "https://platform.deepseek.com/api_keys" },
    moonshot: { href: "https://platform.kimi.ai/console/api-keys" },
    zhipu: { href: "https://docs.bigmodel.cn/cn/guide/develop/apikey" },
    alibaba_model_studio: {
      href: "https://www.alibabacloud.com/help/en/model-studio/get-api-key",
    },
    google_vertex: {
      href: "https://cloud.google.com/iam/docs/creating-managing-service-account-keys",
      label: "Get a service account key",
    },
    aws_bedrock: {
      href: "https://docs.aws.amazon.com/IAM/latest/UserGuide/access-key-self-managed.html",
      label: "Get access keys",
    },
  };
