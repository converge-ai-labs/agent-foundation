"""Compact semantic rows from Chromium's accessibility and DOM snapshots."""

import json
from dataclasses import dataclass, field

from . import BrowserQuery


@dataclass
class DomNode:
    backend: int
    parent: int
    tag: str
    attributes: dict[str, str]
    value: str = ""
    rendered: bool = False
    clickable: bool = False


@dataclass(frozen=True)
class Row:
    backend: int
    role: str
    name: str
    depth: int
    details: str = ""

    def matches(self, query: BrowserQuery) -> bool:
        return (
            (not query.role or self.role.casefold() == query.role.casefold())
            and (not query.name or self.name.casefold() == query.name.casefold())
            and (not query.text or query.text.casefold() in (self.name + " " + self.details).casefold())
        )

    def text(self) -> str:
        return (
            "  " * min(self.depth, 12)
            + f"{self.role}: {json.dumps(self.name[:300], ensure_ascii=False)}"
            + self.details
        )


@dataclass
class Document:
    nodes: dict[int, DomNode] = field(default_factory=dict)

    def subtree(self, root: int) -> set[int]:
        children: dict[int, list[int]] = {}
        for node in self.nodes.values():
            children.setdefault(node.parent, []).append(node.backend)
        result: set[int] = set()
        pending = [root]
        while pending:
            backend = pending.pop()
            if backend not in result:
                result.add(backend)
                pending.extend(children.get(backend, ()))
        return result


def documents(snapshot: dict) -> dict[str, Document]:
    """Decode CDP's columnar representation once per renderer session."""
    strings = snapshot["strings"]
    result = {}
    for document in snapshot["documents"]:
        nodes = document["nodes"]
        identities = nodes["backendNodeId"]
        values = {}
        for key in ("inputValue", "textValue"):
            rare = nodes.get(key, {})
            values.update(
                {
                    index: strings[value] if value >= 0 else ""
                    for index, value in zip(rare.get("index", ()), rare.get("value", ()), strict=True)
                }
            )
        clickable = set(nodes.get("isClickable", {}).get("index", ()))
        layout = document["layout"]
        rendered = {
            index
            for index, bounds, styles in zip(layout["nodeIndex"], layout["bounds"], layout["styles"], strict=True)
            if bounds[2] > 0
            and bounds[3] > 0
            and not any(strings[style] in {"hidden", "collapse", "none"} for style in styles)
        }
        decoded = Document()
        for index, backend in enumerate(identities):
            attrs = nodes["attributes"][index]
            attributes = {strings[attrs[i]]: strings[attrs[i + 1]] for i in range(0, len(attrs), 2)}
            parent = nodes["parentIndex"][index]
            decoded.nodes[backend] = DomNode(
                backend,
                identities[parent] if parent >= 0 else 0,
                strings[nodes["nodeName"][index]].lower(),
                attributes,
                "" if attributes.get("type", "").lower() == "password" else values.get(index, ""),
                index in rendered,
                index in clickable,
            )
        result[strings[document["frameId"]]] = decoded
    return result


def rows(ax_nodes: list[dict], dom: Document, root: int = 0) -> list[Row]:
    """Preserve semantic hierarchy; DOM adds state, input type and otherwise invisible controls."""
    allowed = dom.subtree(root) if root else None
    by_id = {node.get("nodeId", str(index)): node for index, node in enumerate(ax_nodes)}
    children = {child for node in ax_nodes for child in node.get("childIds", ())}
    pending = [(key, 0) for key in reversed(by_id) if key not in children]
    visited = set()
    represented = set()
    result = []
    while pending:
        key, depth = pending.pop()
        if key in visited or key not in by_id:
            continue
        visited.add(key)
        node = by_id[key]
        backend = node.get("backendDOMNodeId", 0)
        role = str(node.get("role", {}).get("value", ""))
        name = str(node.get("name", {}).get("value", ""))
        info = dom.nodes.get(backend)
        attrs = info.attributes if info else {}
        props = {item["name"]: item.get("value", {}).get("value") for item in node.get("properties", ())}
        show = not node.get("ignored") and role not in {"InlineTextBox", "none", "generic"}
        show = show and (allowed is None or backend in allowed)
        if show:
            details = []
            for state in (
                "disabled",
                "readonly",
                "required",
                "checked",
                "selected",
                "expanded",
                "pressed",
                "level",
                "valuemin",
                "valuemax",
            ):
                value = props.get(state)
                if value is not None:
                    details.append(f"{state}={str(value).lower()}")
            value = (
                "[redacted]"
                if attrs.get("type", "").lower() == "password"
                else str(node.get("value", {}).get("value", info.value if info else ""))
            )
            if value:
                details.append("value=" + json.dumps(value[:300], ensure_ascii=False))
            for attr in ("type", "placeholder", "href"):
                if attrs.get(attr):
                    details.append(attr + "=" + json.dumps(attrs[attr][:300], ensure_ascii=False))
            # Text nodes are readable rows, not independent interaction targets.
            reference = backend if role not in {"StaticText", "LineBreak", "RootWebArea"} else 0
            result.append(Row(reference, role, name, depth, " " + " ".join(details) if details else ""))
            represented.add(backend)
        pending.extend((child, depth + int(show)) for child in reversed(node.get("childIds", ())))
    for backend, node in dom.nodes.items():
        if backend in represented or not node.rendered or (allowed is not None and backend not in allowed):
            continue
        attrs = node.attributes
        if node.clickable or node.tag in {"input", "textarea", "select", "canvas", "iframe", "form", "table"}:
            role = attrs.get("role") or ("button" if node.clickable else node.tag)
            name = attrs.get("aria-label") or attrs.get("title") or attrs.get("placeholder") or attrs.get("name", "")
            details = " type=" + json.dumps(attrs["type"]) if "type" in attrs else ""
            result.append(Row(backend, role, name, 0, details))
    return result


# Runs against a document or a resolved region, including its open shadow roots.
# Returns data only: it never issues or replaces element references.
READ_DOCUMENT = r"""function(kind,offset,limit){
    const root=this, doc=root.ownerDocument||root, items=[];
    const visible=e=>e.checkVisibility({visibilityProperty:true});
    const label=e=>e.getAttribute('aria-label')||Array.from(e.labels||[]).map(l=>l.innerText).join(' ')||e.name||'';
    function walk(node){
        if(node.nodeType===3){
            if(kind!=='text'||!node.textContent.trim())return;
            const p=node.parentElement;
            if(!p||['SCRIPT','STYLE','NOSCRIPT','TEXTAREA','OPTION'].includes(p.tagName)||!visible(p))return;
            const range=doc.createRange();range.selectNode(node);
            if(!Array.from(range.getClientRects()).some(r=>r.width&&r.height))return;
            const text=node.textContent.trim();
            for(let i=0;i<text.length;i+=1000)items.push({text:text.slice(i,i+1000)});
            return;
        }
        if(node.nodeType===1){
            const e=node;
            if(kind==='links'&&e.matches('a[href]')&&visible(e))items.push({text:e.innerText||label(e),url:e.href});
            if(kind==='tables'&&e.matches('tr,[role=row]')&&visible(e)){
                const table=e.closest('table,[role=table],[role=grid]');
                if(table)items.push({table:label(table)||table.querySelector('caption')?.innerText||'',
                    cells:Array.from(e.querySelectorAll('th,td,[role=cell],[role=columnheader],[role=rowheader]'))
                        .filter(c=>c.closest('tr,[role=row]')===e).map(c=>({text:c.innerText,
                            header:c.matches('th,[role=columnheader],[role=rowheader]'),rowspan:c.rowSpan||1,colspan:c.colSpan||1}))});
            }
            if(kind==='forms'&&e.matches('input,textarea,select,[contenteditable=true]')&&visible(e)){
                const item={name:label(e),type:e.type||e.tagName.toLowerCase(),
                    value:e.type==='password'?'[redacted]':e.value??e.innerText,
                    disabled:e.matches(':disabled'),readonly:!!e.readOnly,required:!!e.required};
                if(['checkbox','radio'].includes(e.type))item.checked=e.checked;
                if(e.tagName==='SELECT')item.options=Array.from(e.options).map(o=>({value:o.value,label:o.label,selected:o.selected,disabled:o.disabled}));
                if(e.type==='file'){delete item.value;item.files=Array.from(e.files).map(f=>f.name);}
                items.push(item);
            }
            if(e.shadowRoot){walk(e.shadowRoot);return;}
            if(e.tagName==='SLOT'){for(const n of e.assignedNodes({flatten:true}))walk(n);return;}
        }
        for(const child of node.childNodes)walk(child);
    }
    walk(root);
    const selected=[];let size=0,truncated=false;
    for(const item of items.slice(offset,offset+limit)){
        const encoded=JSON.stringify(item);
        if(size+encoded.length>20000){
            if(!selected.length){selected.push({excerpt:encoded.slice(0,18000),truncated:true});truncated=true;}
            break;
        }
        selected.push(item);size+=encoded.length;
    }
    const next=offset+selected.length;
    return {items:selected,total:items.length,offset,next_offset:next<items.length?next:null,truncated};
}"""
