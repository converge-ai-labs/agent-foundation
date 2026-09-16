import fs from "node:fs/promises";
import openapiTS, { astToString } from "openapi-typescript";
import prettier from "prettier";
import ts from "typescript";

const source = new URL(
  "../../../proto/a13n-service/openapi.json",
  import.meta.url,
);
const target = new URL("./src/service-client/schema.ts", import.meta.url);
const ast = await openapiTS(source, {
  defaultNonNullable: false,
  transform(schema) {
    if (schema.format === "binary")
      return ts.factory.createTypeReferenceNode("Binary");
  },
});
const content = await prettier.format(
  astToString(ast) +
    "\nexport type Binary = Blob | ReadableStream<Uint8Array>;\n",
  { parser: "typescript" },
);
if (process.argv.includes("--check")) {
  if ((await fs.readFile(target, "utf8")) !== content) {
    throw new Error(
      "Console Service types changed. Run make service-contract-generate.",
    );
  }
} else {
  await fs.writeFile(target, content);
}
