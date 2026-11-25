import {
  defaultTreeAdapter as tree,
  html as namespaces,
  parse,
  parseFragment,
  serializeOuter,
  type DefaultTreeAdapterTypes,
} from "parse5";

type Element = DefaultTreeAdapterTypes.Element;

const htmlElements = new Set(
  "html head body title style a abbr address article aside b bdi bdo blockquote br button caption cite code col colgroup dd del details dfn div dl dt em figcaption figure footer h1 h2 h3 h4 h5 h6 header hgroup hr i img input ins kbd label li main mark meter nav ol p pre progress q rp rt ruby s samp section small span strong sub summary sup table tbody td textarea tfoot th thead time tr u ul var wbr".split(
    " ",
  ),
);
const svgElements = new Set(
  "svg a circle clipPath defs desc ellipse g line linearGradient marker mask path pattern polygon polyline radialGradient rect stop symbol text textPath title tspan use".split(
    " ",
  ),
);
const htmlAttributes = new Set(
  "alt checked colspan datetime dir disabled height lang max min name open placeholder readonly reversed role rowspan scope span start step type value width".split(
    " ",
  ),
);
const svgAttributes = new Set(
  "clip-path clip-rule clipPathUnits cx cy d dx dy fill fill-opacity fill-rule fx fy gradientTransform gradientUnits height marker-end marker-mid marker-start markerHeight markerUnits markerWidth mask maskContentUnits maskUnits offset opacity orient patternContentUnits patternTransform patternUnits points preserveAspectRatio r refX refY rx ry spreadMethod stop-color stop-opacity stroke stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-opacity stroke-width text-anchor textLength transform vector-effect viewBox width x x1 x2 y y1 y2".split(
    " ",
  ),
);

const CSP =
  "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'";
const fragmentDefaults = `<style>
:root { color-scheme: light dark; }
body { margin: 16px; color: CanvasText; background: Canvas; font-family: system-ui, sans-serif; }
</style>`;

function filterContents(parent: Element, depth = 0) {
  const svg = parent.namespaceURI === namespaces.NS.SVG;
  parent.attrs = parent.attrs.filter(({ name, value, namespace }) => {
    if (svg && name === "href") {
      return parent.tagName !== "a" && value.startsWith("#");
    }
    if (namespace) return false;
    if (name === "src" && parent.tagName === "img") {
      return /^data:image\/(?:avif|gif|jpeg|png|webp);base64,/i.test(value);
    }
    return (
      name === "id" ||
      name === "class" ||
      name === "style" ||
      name === "title" ||
      name.startsWith("aria-") ||
      name.startsWith("data-") ||
      (svg ? svgAttributes : htmlAttributes).has(name)
    );
  });
  // Bound recursion in both filtering and serialization. The parser itself is inert.
  parent.childNodes = parent.childNodes.filter((child) => {
    if (tree.isTextNode(child)) return true;
    if (!tree.isElementNode(child) || depth >= 80) return false;
    if (
      child.namespaceURI === namespaces.NS.HTML
        ? !htmlElements.has(child.tagName)
        : child.namespaceURI !== namespaces.NS.SVG ||
          !svgElements.has(child.tagName)
    ) {
      return false;
    }
    filterContents(child, depth + 1);
    return true;
  });
}

export function buildHtmlPreviewDocument(source: string): string {
  // Do not give original markup to a browser parser that can load resources.
  const document = parse(source, { scriptingEnabled: false });
  const fullDocument = document.childNodes.some(tree.isDocumentTypeNode);
  const root = document.childNodes.find(tree.isElementNode)!;
  filterContents(root);
  const head = root.childNodes.find(tree.isElementNode)!;
  const { childNodes: policy } = parseFragment(
    `<meta http-equiv="Content-Security-Policy" content="${CSP}">${fullDocument ? "" : fragmentDefaults}`,
  );
  head.childNodes.unshift(...policy);
  return `<!DOCTYPE html>${serializeOuter(root)}`;
}
