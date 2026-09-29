import type { OpenAPIPageProps_Spec } from "fumadocs-openapi/ui";

type Document = OpenAPIPageProps_Spec["payload"]["bundled"];
type Json = Record<string, unknown>;

const METHODS = new Set([
  "get",
  "put",
  "post",
  "delete",
  "options",
  "head",
  "patch",
  "trace",
]);
const COMPONENT_REF = "#/components/";

/**
 * Narrows an operation page's document to its operations and the components they reference.
 * The API page is a client component, so the whole document would otherwise be serialized
 * into every operation page.
 */
export function withPageDocument(
  props: OpenAPIPageProps_Spec,
): OpenAPIPageProps_Spec {
  const document = props.payload.bundled;
  const allPaths = (document.paths ?? {}) as Record<string, Json>;
  const allComponents = (document.components ?? {}) as Record<string, Json>;

  const paths: Record<string, Json> = {};
  for (const { path, method } of props.operations ?? []) {
    paths[path] ??= Object.fromEntries(
      Object.entries(allPaths[path]).filter(([key]) => !METHODS.has(key)),
    );
    paths[path][method] = allPaths[path][method];
  }

  // Security schemes are referenced by name, not `$ref`.
  const components: Record<string, Json> = {
    securitySchemes: allComponents.securitySchemes ?? {},
  };
  const visit = (node: unknown): void => {
    if (typeof node !== "object" || node === null) return;
    const ref = (node as { $ref?: unknown }).$ref;
    if (typeof ref === "string" && ref.startsWith(COMPONENT_REF)) {
      const [kind, name] = ref.slice(COMPONENT_REF.length).split("/");
      if (!components[kind]?.[name]) {
        const component = allComponents[kind]?.[name];
        if (component === undefined) {
          throw new Error(`Unresolved OpenAPI reference: ${ref}`);
        }
        (components[kind] ??= {})[name] = component;
        visit(component);
      }
    }
    Object.values(node).forEach(visit);
  };
  visit(paths);

  return {
    ...props,
    payload: {
      ...props.payload,
      bundled: { ...document, paths, components } as Document,
    },
  };
}
