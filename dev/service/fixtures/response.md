# Local project review / 本地项目评审

All names, figures and recommendations in this response are fictional test content.

## Findings

The sample team is preparing a release of a small documentation assistant. The primary workflow is to collect source material, compare revisions and prepare a reviewable summary.

| Area        | Observation                             | Next step                     |
| ----------- | --------------------------------------- | ----------------------------- |
| Navigation  | Long workspace names need enough room   | Check narrow windows          |
| Sessions    | Readers often return to older answers   | Preserve scroll position      |
| Attachments | Filenames contain both English and 中文 | Keep download actions visible |

## Example

```python
from pathlib import Path

for document in Path("notes").glob("*.md"):
    print(document.name)
```

> Review note: a failed request should retain the user's draft and make retry possible.

## 中文说明

这是一段用于检查阅读体验的虚构内容。请尝试缩小窗口、切换会话、滚动到历史消息，再回到最新消息。内容中包含**加粗文字**、*强调文字*和 `inline code`，用于观察不同文本样式的表现。

1. Open a long conversation.
2. Scroll to an earlier answer.
3. Send another message and inspect the reading position.
4. Refresh the page and verify the retained content.

## Longer discussion

A useful review keeps the original question visible and separates evidence from recommendations. The conversation may span multiple attempts, include uploaded documents, and contain both brief follow-up questions and long explanations.

Repeated paragraphs here are intentional sample content for scrolling. They do not describe real customers or actual operational results.

The review remains open until a human has checked the interaction. Keyboard navigation, focus restoration and clear loading feedback are part of the same experience.
