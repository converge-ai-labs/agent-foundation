/** Where a provider hands out the secret its connect step asks for. */
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
    minimax: {
      href: "https://platform.minimax.io/docs/guides/quickstart-preparation",
    },
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
    e2b: { href: "https://e2b.dev/dashboard?tab=keys" },
    daytona: { href: "https://app.daytona.io/dashboard/keys" },
    modal: {
      href: "https://modal.com/settings/tokens",
      label: "Get an API token",
    },
    vercel: {
      href: "https://vercel.com/account/settings/tokens",
      label: "Get an access token",
    },
    sprites: {
      href: "https://fly.io/user/personal_access_tokens",
      label: "Get an access token",
    },
    runloop: { href: "https://platform.runloop.ai/settings" },
    "a13n.mem0-platform": { href: "https://app.mem0.ai/dashboard/api-keys" },
    composio: { href: "https://platform.composio.dev/settings" },
  };
