from a13n_harness_ui.resource_names import coding_agent_name, model_name


def test_plain_generated_formatting_preserves_custom_unicode_and_case() -> None:
    assert model_name("openai-chat", "组织/模型-AbC") == "OpenAI Chat - 组织/模型-AbC"
    assert coding_agent_name("我的模型") == "我的模型 - Coding"
    assert len(model_name("openai-chat", "x" * 256)) == 110
    assert len(coding_agent_name("x" * 256)) <= 128
